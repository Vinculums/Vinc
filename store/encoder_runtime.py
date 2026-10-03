"""본문·질의 벡터를 만드는 세 패키지의 실제 판을 한 지문으로 묶는다."""

from importlib.metadata import PackageNotFoundError, version


_PACKAGES = (("onnxruntime", "ort"), ("tokenizers", "tok"), ("numpy", "np"))


def fingerprint() -> str:
    """세 판이 모두 있을 때만 지문을 낸다. 하나라도 없으면 비교 가능한 척하지 않는다.

    호스티드의 사이드카와 인코더는 같은 이미지 SHA 로 뜬다.
    """
    parts = []
    for package, label in _PACKAGES:
        try:
            parts.append(f"{label}={version(package)}")
        except PackageNotFoundError:
            return ""
    return ";".join(parts)
