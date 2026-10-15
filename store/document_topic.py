"""문서 목록과 이동 검사가 공유하는 주제 우선순위."""
from __future__ import annotations


def ranked_topics(mentions: dict[str, int], authored: str = "") -> list[str]:
    """명시한 주제가 우선이고, 언급 수 동률은 주제 이름순이다."""
    found = [topic for topic, _ in sorted(mentions.items(), key=lambda item: (-item[1], item[0]))]
    return [authored] + [topic for topic in found if topic != authored] if authored else found
