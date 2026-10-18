"""Persisted extraction scope; geometry checks cannot establish drawing completeness."""
from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing import Literal


class ExtractedEntity(BaseModel):
    model_config = ConfigDict(extra='forbid')
    handle: str = Field(max_length=128)
    entity_type: str = Field(max_length=128)
    layer: str = Field(default='', max_length=256)
    reason: str = Field(min_length=1, max_length=2000)


class ExtractionCoverage(BaseModel):
    model_config = ConfigDict(extra='forbid')
    schema_version: Literal['1.0'] = '1.0'
    adapter: str = Field(default='json', max_length=40)
    scope: str = Field(default='explicit-engineering-model', max_length=256)
    status: Literal['complete', 'incomplete', 'unverified'] = 'unverified'
    mapped_entities: int = Field(default=0, ge=0, le=10000)
    unmapped_entities: int = Field(default=0, ge=0, le=10000)
    unresolved_ids: list[str] = Field(default_factory=list, max_length=10000)
    unresolved_entities: list[ExtractedEntity] = Field(default_factory=list, max_length=10000)
    ignored_entities: list[ExtractedEntity] = Field(default_factory=list, max_length=10000)
    static_metadata_ids: list[str] = Field(default_factory=list, max_length=1100)
    source_generation: int = Field(default=0, ge=0)
    environment: str = Field(default='CAD extraction completeness has not been established.', max_length=4000)

    @model_validator(mode='after')
    def consistent(self):
        if self.status == 'complete' and (self.unmapped_entities or self.unresolved_ids or self.unresolved_entities or self.static_metadata_ids):
            raise ValueError('Complete extraction cannot contain unresolved or static source geometry')
        if self.status != 'incomplete' and (self.unmapped_entities or self.unresolved_ids or self.unresolved_entities):
            raise ValueError('Unresolved source geometry requires incomplete extraction status')
        return self


def checked_coverage(value=None, adapter='json'):
    if value is None:
        value = dict(adapter=adapter)
    if isinstance(value, ExtractionCoverage):
        value = value.model_dump(mode='json')
    return ExtractionCoverage.model_validate(value).model_dump(mode='json')


def coverage_validation(coverage):
    complete = coverage['status'] == 'complete'
    return dict(requirement='cad_extraction_coverage', status='PASS' if complete else 'UNVERIFIED',
                status_code='PASS' if complete else 'UNVERIFIED', severity='info' if complete else 'warning',
                detail='All entities in the declared extraction scope are mapped or explicitly classified.' if complete else coverage['environment'],
                measured=coverage['mapped_entities'], required='Complete declared CAD extraction scope',
                object_ids=coverage['unresolved_ids'], location=None,
                suggested_correction=None if complete else 'Resolve source bindings and classify omitted CAD geometry before relying on drawing-wide clearance.')
