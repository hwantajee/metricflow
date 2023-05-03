from typing import Dict, List, Set

from dbt_semantic_interfaces.objects.data_source import DataSource
from dbt_semantic_interfaces.objects.elements.entity import Entity
from dbt_semantic_interfaces.objects.user_configured_model import UserConfiguredModel
from dbt_semantic_interfaces.references import DataSourceElementReference, EntityReference
from metricflow.model.validations.validator_helpers import (
    DataSourceElementContext,
    DataSourceElementType,
    FileContext,
    ModelValidationRule,
    ValidationWarning,
    validate_safely,
    ValidationIssue,
)


class CommonEntitysRule(ModelValidationRule):
    """Checks that identifiers exist on more than one data source"""

    @staticmethod
    def _map_data_source_entities(data_sources: List[DataSource]) -> Dict[EntityReference, Set[str]]:
        """Generate mapping of identifier names to the set of data_sources where it is defined"""
        identifiers_to_data_sources: Dict[EntityReference, Set[str]] = {}
        for data_source in data_sources or []:
            for identifier in data_source.identifiers or []:
                if identifier.reference in identifiers_to_data_sources:
                    identifiers_to_data_sources[identifier.reference].add(data_source.name)
                else:
                    identifiers_to_data_sources[identifier.reference] = {data_source.name}
        return identifiers_to_data_sources

    @staticmethod
    @validate_safely(whats_being_done="checking identifier exists on more than one data source")
    def _check_entity(
        identifier: Entity,
        data_source: DataSource,
        identifiers_to_data_sources: Dict[EntityReference, Set[str]],
    ) -> List[ValidationIssue]:
        issues: List[ValidationIssue] = []
        # If the identifier is the dict and if the set of data sources minus this data source is empty,
        # then we warn the user that their identifier will be unused in joins
        if (
            identifier.reference in identifiers_to_data_sources
            and len(identifiers_to_data_sources[identifier.reference].difference({data_source.name})) == 0
        ):
            issues.append(
                ValidationWarning(
                    context=DataSourceElementContext(
                        file_context=FileContext.from_metadata(metadata=data_source.metadata),
                        data_source_element=DataSourceElementReference(
                            data_source_name=data_source.name, element_name=identifier.name
                        ),
                        element_type=DataSourceElementType.IDENTIFIER,
                    ),
                    message=f"Entity `{identifier.reference.element_name}` "
                    f"only found in one data source `{data_source.name}` "
                    f"which means it will be unused in joins.",
                )
            )
        return issues

    @staticmethod
    @validate_safely(whats_being_done="running model validation warning if identifiers are only one one data source")
    def validate_model(model: UserConfiguredModel) -> List[ValidationIssue]:
        """Issues a warning for any identifier that is associated with only one data_source"""
        issues = []

        identifiers_to_data_sources = CommonEntitysRule._map_data_source_entities(model.data_sources)
        for data_source in model.data_sources or []:
            for identifier in data_source.identifiers or []:
                issues += CommonEntitysRule._check_entity(
                    identifier=identifier,
                    data_source=data_source,
                    identifiers_to_data_sources=identifiers_to_data_sources,
                )

        return issues
