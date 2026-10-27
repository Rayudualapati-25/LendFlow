from fastapi import APIRouter

from app.domain.products import CATALOGUE
from app.schemas import ProductOut

router = APIRouter(tags=["products"])


@router.get("/products")
def list_products() -> list[ProductOut]:
    """The loan products on offer, with their fixed rate and limits. No sign-in needed."""
    return [ProductOut.model_validate(terms) for terms in CATALOGUE.values()]
