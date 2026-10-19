"""Fixed knowledge-graph schema (Project 3). The LLM may only emit these entity/relation types; anything else is rejected."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

EntityType = Literal["PERSON", "ORGANIZATION", "LOCATION", "WORK", "EVENT", "DATE", "PRODUCT", "OTHER"]
RelationType = Literal["BORN_IN", "DIED_IN", "LOCATED_IN", "PART_OF", "MEMBER_OF", "EMPLOYED_BY", "FOUNDED_BY",
                       "AUTHOR_OF", "DIRECTED_BY", "STARRED_IN", "PRODUCED_BY", "RELEASED_IN", "SPOUSE_OF",
                       "PARENT_OF", "EDUCATED_AT", "CAPITAL_OF", "OWNED_BY", "NAMED_AFTER", "RELATED_TO"]
ENTITY_TYPES = list(EntityType.__args__)
RELATION_TYPES = list(RelationType.__args__)

# Common synonyms an LLM tends to emit -> the fixed enum. Unknown values are REJECTED, never invented into the graph.
ENTITY_ALIASES = {"ORG": "ORGANIZATION", "COMPANY": "ORGANIZATION", "GPE": "LOCATION", "PLACE": "LOCATION", "CITY": "LOCATION",
                  "COUNTRY": "LOCATION", "FILM": "WORK", "MOVIE": "WORK", "BOOK": "WORK", "ALBUM": "WORK", "SONG": "WORK",
                  "PERSON_NAME": "PERSON", "NORP": "OTHER", "TIME": "DATE", "YEAR": "DATE"}
RELATION_ALIASES = {"BORN": "BORN_IN", "BIRTHPLACE": "BORN_IN", "DIRECTED_BY": "DIRECTED_BY", "DIRECTOR": "DIRECTED_BY",
                    "AUTHOR": "AUTHOR_OF", "WROTE": "AUTHOR_OF", "FOUNDER": "FOUNDED_BY", "LOCATED": "LOCATED_IN",
                    "SPOUSE": "SPOUSE_OF", "MARRIED_TO": "SPOUSE_OF", "STUDIED_AT": "EDUCATED_AT", "ALMA_MATER": "EDUCATED_AT",
                    "ACTED_IN": "STARRED_IN", "RELEASED": "RELEASED_IN"}


class Entity(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    type: EntityType

    @field_validator("name")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = " ".join(v.split())
        if not v:
            raise ValueError("blank entity name")
        return v


class Relation(BaseModel):
    source: str = Field(min_length=1, max_length=120)
    type: RelationType
    target: str = Field(min_length=1, max_length=120)


class GroundedRelation(BaseModel):
    """A validated relation + the chunk it was extracted from. `evidence_chunk_id` is set by OUR code, never the LLM."""
    source: str
    type: RelationType
    target: str
    evidence_chunk_id: str


class ChunkExtraction(BaseModel):
    chunk_id: str
    entities: list[Entity] = Field(default_factory=list)
    relations: list[GroundedRelation] = Field(default_factory=list)
