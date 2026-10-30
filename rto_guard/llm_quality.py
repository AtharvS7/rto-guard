from __future__ import annotations
import os
from typing import Literal
from pydantic import BaseModel, Field


class AddressAssessment(BaseModel):
    name_plausible: bool = Field(description="Whether the name resembles a plausible human/customer name; do not infer identity.")
    address_usable: bool = Field(description="Whether a courier could reasonably locate the delivery using this address and city.")
    address_completeness: Literal["complete", "partial", "poor"]
    missing_details: list[str]
    normalized_address: str
    explanation: str


def is_llm_configured() -> bool:
    return bool(os.getenv("OPENAI_API_KEY", "").strip())


def review_address_with_llm(name: str, address: str, city: str, state: str) -> dict:
    """Optional LLM review for Hinglish/free-text address interpretation.

    No LLM score can cancel an order. Its result is a soft quality signal only.
    Customer name and address are sent to the configured API only after an
    explicit in-app opt-in.
    """
    if not is_llm_configured():
        raise RuntimeError("OPENAI_API_KEY is not configured")
    from openai import OpenAI

    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"], timeout=30, max_retries=1)
    model_name = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    response = client.responses.parse(
        model=model_name,
        input=[
            {
                "role": "system",
                "content": (
                    "You are an address-quality assistant for Indian e-commerce deliveries. "
                    "Interpret English, Hindi transliteration and Hinglish such as 'mandir ke saamne', "
                    "'station chowk se left' and 'ph krke aana'. Assess only whether the delivery details "
                    "are actionable. Do not assume a name is fake merely because it is unfamiliar, "
                    "regional, transliterated or culturally uncommon. Never infer caste, religion, "
                    "ethnicity or other sensitive traits. Do not estimate RTO probability or recommend "
                    "cancellation. If uncertain, mark partial and explain what to confirm."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Customer name: {name}\nAddress: {address}\nCity: {city}\nState: {state}\n"
                    "Return the structured address-quality assessment."
                ),
            },
        ],
        text_format=AddressAssessment,
    )
    parsed = response.output_parsed
    if parsed is None:
        raise RuntimeError("The AI service did not return a structured address assessment")
    return parsed.model_dump()
