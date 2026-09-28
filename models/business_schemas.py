from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class BusinessProfile(BaseModel):
    company_name: str
    industry: str
    description: str
    keywords: List[str] = Field(default_factory=list)


class ProductInfo(BaseModel):
    product_id: str
    name: str
    description: str
    keywords: List[str] = Field(default_factory=list)


class AudienceProfile(BaseModel):
    target_platforms: List[str] = Field(default_factory=list)
    demographics: Dict[str, Any] = Field(default_factory=dict)
    interests: List[str] = Field(default_factory=list)


class BusinessContextSchema(BaseModel):
    business: BusinessProfile
    products: List[ProductInfo] = Field(default_factory=list)
    audience: AudienceProfile
