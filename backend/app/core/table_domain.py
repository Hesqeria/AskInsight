"""Table domain segmentation for large-scale databases (50+ tables).

When the database has many tables, full Milvus recall and LLM context
become bottlenecks. This module groups tables by business domain and
enables two-phase recall: keyword→domain→Milvus search within domain.

Domain auto-detection rules:
  - order/payment/cart/comment → sales
  - user/member/customer → customer
  - sku/product/spu/category/brand → product
  - province/region → geography
  - activity/coupon/promotion → marketing
  - log/page/action/display → traffic
  - gmv/revenue → finance
"""
from enum import Enum
from collections import defaultdict
from app.core.log import logger


class Domain(str, Enum):
    SALES = "sales"
    CUSTOMER = "customer"
    PRODUCT = "product"
    GEOGRAPHY = "geography"
    MARKETING = "marketing"
    TRAFFIC = "traffic"
    FINANCE = "finance"
    GENERAL = "general"


# Keyword → Domain mapping
_DOMAIN_KEYWORDS = {
    "order": Domain.SALES, "payment": Domain.SALES, "cart": Domain.SALES,
    "comment": Domain.SALES, "refund": Domain.SALES, "favor": Domain.SALES,
    "user": Domain.CUSTOMER, "member": Domain.CUSTOMER, "customer": Domain.CUSTOMER,
    "sku": Domain.PRODUCT, "spu": Domain.PRODUCT, "product": Domain.PRODUCT,
    "category": Domain.PRODUCT, "brand": Domain.PRODUCT, "trademark": Domain.PRODUCT,
    "province": Domain.GEOGRAPHY, "region": Domain.GEOGRAPHY,
    "activity": Domain.MARKETING, "coupon": Domain.MARKETING, "promotion": Domain.MARKETING,
    "log": Domain.TRAFFIC, "page": Domain.TRAFFIC, "action": Domain.TRAFFIC,
    "display": Domain.TRAFFIC, "visitor": Domain.TRAFFIC,
    "gmv": Domain.FINANCE, "revenue": Domain.FINANCE,
}


def detect_domain(table_name: str) -> Domain:
    """Auto-detect domain from table name."""
    name_lower = table_name.lower()
    for keyword, domain in _DOMAIN_KEYWORDS.items():
        if keyword in name_lower:
            return domain
    return Domain.GENERAL


def group_tables_by_domain(table_infos: list[dict]) -> dict[str, list[dict]]:
    """Group tables by domain.

    Returns:
        {domain_name: [table_info, ...]}
    """
    groups = defaultdict(list)
    for t in table_infos:
        domain = detect_domain(t.get("name", ""))
        groups[domain.value].append(t)
    return dict(groups)


def select_top_tables_by_domain(table_infos: list[dict], max_per_domain: int = 8,
                                max_total: int = 25) -> list[dict]:
    """Select the most relevant tables across domains.

    For large-scale databases (50+ tables), this reduces DDL size
    and improves LLM accuracy by limiting context.

    Strategy:
      1. Group tables by domain
      2. Take top N from each domain (fact tables first, then dim)
      3. Cap total at max_total
    """
    groups = group_tables_by_domain(table_infos)
    selected = []
    
    for domain, tables in groups.items():
        # Sort: fact tables first, then dim
        facts = [t for t in tables if t.get("role") in ("fact", "measure")]
        dims = [t for t in tables if t.get("role") not in ("fact", "measure")]
        domain_tables = facts + dims
        selected.extend(domain_tables[:max_per_domain])
    
    # Cap total
    if len(selected) > max_total:
        selected = selected[:max_total]
    
    logger.info(
        f"Domain segmentation: {len(table_infos)} tables -> "
        f"{len(selected)} selected (groups: {list(groups.keys())})"
    )
    return selected


def get_user_query_domains(query: str) -> list[str]:
    """Detect domains from user query keywords.

    Returns list of domain values to scope the search.
    """
    query_lower = query.lower()
    matched = []
    for keyword, domain in _DOMAIN_KEYWORDS.items():
        if keyword in query_lower and domain.value not in matched:
            matched.append(domain.value)
    return matched if matched else ["general"]
