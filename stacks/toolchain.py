""" Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved. """
""" SPDX-License-Identifier: MIT-0 """

import constants
import aws_cdk as cdk

from typing import Any
from aws_cdk import (
    pipelines as _pipelines,
    aws_ssm as _ssm,
    aws_iam as _iam,
    aws_codebuild as _codebuild
)
from stacks.infrastructure import InfrastructureStack
from stacks.tuning import TuningStack
from constructs import Construct
from cdk_nag import NagSuppressions

class ToolChainStack(cdk.Stack):

    def __init__(self, scope: Construct, id: str, **kwargs: Any):
        super().__init__(scope, id, **kwargs)

        # Load pipeline variables form toolchain context
        context = self.node.try_get_context("toolchain-context")

        # Set the GitHub repo as the source
        source = _pipelines.CodePipelineSource.connection(
            constants.GITHUB_REPO_NAME,
            "main",
            connection_arn=constants.CODESTAR_CONNECTION_ARN
        )

        # Create a placeholder parameter for a custom fine-tuned model
        # NOTE: This parameter is a placeholder, to be updated after
        #       fine tuning.
        model_parameter = _ssm.StringParameter(
            self,
            "CustomModelParameter",
            description="Custom Model Name",
            parameter_name="CustomModelName",
            string_value="PLACEHOLDER"
        )

        # Create the Pipeline synth role
        synth_role = _iam.Role(
            self,
            "SynthRole",
            assumed_by=_iam.CompositePrincipal(
                _iam.ServicePrincipal("codebuild.amazonaws.com"),
            ),
            managed_policies=[
                _iam.ManagedPolicy.from_aws_managed_policy_name("AdministratorAccess")
            ]
        )

        # Create the CDK Pipeline skeleton
        pipeline = _pipelines.CodePipeline(
            self,
            "CDKPipeline",
            pipeline_name=f"{constants.WORKLOAD_NAME}-Pipeline",
            self_mutation=True,
            cli_version=context.get("cdk-version"),
            cross_account_keys=True,
            synth=_pipelines.CodeBuildStep(
                "Synth",
                input=source,
                role=synth_role,
                install_commands=[
                    "printenv",
                    f"npm install -g aws-cdk@{context.get('cdk-version')}",
                    "python -m pip install -U pip",
                    "pip install -r requirements.txt",
                ],
                commands=[
                    "cdk synth"
                ]
            ),
        )

        # Add QA Stage
        ToolChainStack._add_stage(
            pipeline=pipeline,
            stage_name=constants.QA_ENV_NAME,
            stage_account=self.account,
            stage_region=self.region,
            model_parameter_name="CustomModelName"
        )

        # Add Production Stage
        # ToolChainStack._add_stage(
        #     pipeline=pipeline,
        #     stage_name=constants.PROD_ENV_NAME,
        #     stage_account=self.account,
        #     stage_region=self.region,
        #     model_parameter_name="CustomModelName"
        # )

        # Add tuning stack as CT stage
        # ToolChainStack._add_stage(
        #     pipeline=pipeline,
        #     stage_name="TUNING",
        #     stage_account=self.account,
        #     stage_region=self.region,
        #     model_parameter_name="CustomModelName"
        # )

        # AWS CDK Pipeline CDK NAG suppressions
        # NagSuppressions.add_resource_suppressions(
        #     construct=synth_role,
        #     suppressions=[
        #         {
        #             "id": "AwsSolutions-IAM4",
        #             "reason": "CDK Pipeline synthesis requires 'AdministratorAccess' as cloudformation execution policy"
        #         }
        #     ],
        #     apply_to_children=True
        # )
        # NagSuppressions.add_resource_suppressions(
        #     construct=pipeline,
        #     suppressions=[
        #         {
        #             "id": "AwsSolutions-KMS5",
        #             "reason": "Using the default CDK Pipeline construct, KMS Key settings are managed by the CDK construct"
        #         },
        #         {
        #             "id": "AwsSolutions-S1",
        #             "reason": "Using the default CDK Pipeline construct, S3 artifacts bucket is managed by the CDK construct"
        #         },
        #         {
        #             "id": "AwsSolutions-IAM5",
        #             "reason": "Using the default CDK Pipeline construct, S3 artifacts bucket is managed by the CDK construct"
        #         }
        #     ],
        #     apply_to_children=True
        # )

    @staticmethod
    def _add_stage(pipeline: _pipelines.CodePipeline, stage_name: str, stage_account: str, stage_region: str, model_parameter_name: str=None) -> None:
        stage = cdk.Stage(
            pipeline,
            stage_name,
            env=cdk.Environment(account=stage_account, region=stage_region)
        )
        if stage_name == constants.QA_ENV_NAME:
            infrastructure = InfrastructureStack(
                stage,
                f"{constants.WORKLOAD_NAME}-{stage_name}",
                stack_name=f"{constants.WORKLOAD_NAME}-{stage_name}",
                model_parameter_name=model_parameter_name
            )
            pipeline.add_stage(
                stage,
                post=[
                    _pipelines.ShellStep(
                        "SystemTests",
                        env_from_cfn_outputs={
                            "TEXT_ENDPOINT": infrastructure.text_apigw_output,
                            "RAG_ENDPOINT": infrastructure.rag_apigw_output
                        },
                        install_commands=[
                            "printenv",
                            "python -m pip install -U pip",
                            "pip install -r ./tests/requirements.txt"
                        ],
                        commands=[
                            "pytest ./tests/system_test.py"
                        ]
                    )
                ]
            )
        elif stage_name == constants.PROD_ENV_NAME:
            InfrastructureStack(
                stage,
                f"{constants.WORKLOAD_NAME}-{stage_name}",
                stack_name=f"{constants.WORKLOAD_NAME}-{stage_name}",
                model_parameter_name=model_parameter_name
            )
            pipeline.add_stage(stage)
        else:
            TuningStack(
                stage,
                f"{constants.WORKLOAD_NAME}-{stage_name}",
                stack_name=f"{constants.WORKLOAD_NAME}-{stage_name}",
                pipeline_name=f"{constants.WORKLOAD_NAME}-Pipeline",
                model_parameter=model_parameter_name
            )
            pipeline.add_stage(stage)
