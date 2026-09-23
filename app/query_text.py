"""Normalize product nouns, preserving ratings, SKU and user constraints."""
import re


NOUN_ALIASES = (
    (r'лампоч(?:к(?:а|и|у|е|ой|ою|ам|ами|ах)|ек)', 'лампа'),
    (r'розеточ(?:к(?:а|и|у|е|ой|ою|ам|ами|ах)|ек)', 'розетка'),
    (r'проводоч(?:ек|ка|ки|ков|ку|ке|ком|кам|ками|ках)', 'провод'),
    (r'кабел(?:[её]к|ьк(?:а|и|ов|у|е|ом|ам|ами|ах))', 'кабель'),
    (r'автоматик(?:а|и|ов|у|е|ом|ам|ами|ах)?', 'автомат'),
)


def normalize_catalog_terms(text: str) -> str:
    for pattern, noun in NOUN_ALIASES:
        text = re.sub(r'\b(?:' + pattern + r')\b', noun, text, flags=re.I)
    return text


def explicit_purchase_refusal(text: str) -> bool:
    # A whole utterance, not a substring: "мне нужно" is a positive request.
    # Negative product preferences ("не нужно дорогое, подбери дешевле")
    # must not cancel the purchase or its pending confirmation.
    prefix = r'(?:(?:нет|спасибо|я|мне|пока|больше|ничего)[\s,!.]+)*'
    refusal = r'(?:не\s+(?:буду|хочу)\s+покупать|не\s+(?:надо|нужно)|отказываюсь(?:\s+от\s+покупки)?|сатып\s+алмаймын|керек\s+емес)'
    suffix = r'(?:[\s,!.]+(?:ничего|пока|больше|спасибо|вообще|уже))*[\s.!?,]*'
    return bool(re.fullmatch(prefix + refusal + suffix, text.casefold().strip()))
