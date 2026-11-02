"""본문·질의 벡터를 만드는 세 패키지의 실제 판을 한 지문으로 묶는다."""

from importlib.metadata import PackageNotFoundError, version


_PACKAGES = (("onnxruntime", "ort"), ("tokenizers", "tok"), ("numpy", "np"))
# GPU 판(onnxruntime-gpu)은 같은 모듈을 다른 배포판 이름으로 싣는다. 라벨을 갈라 둔다
# (CUDA 와 CPU 의 fp32 결과는 아주 조금 다르므로 같은 지문이면 안 된다). _PACKAGES 의 모양은
# scripts/repair_hub_store.py 도 읽으므로 바꾸지 않는다.
_ALTERNATES = {"onnxruntime": ("onnxruntime-gpu", "ort-gpu")}


def fingerprint() -> str:
    """세 판이 모두 있을 때만 지문을 낸다. 하나라도 없으면 비교 가능한 척하지 않는다.

    호스티드의 사이드카와 인코더는 같은 이미지 SHA 로 뜬다.
    """
    parts = []
    for package, label in _PACKAGES:
        try:
            parts.append(f"{label}={version(package)}")
        except PackageNotFoundError:
            alt = _ALTERNATES.get(package)
            try:
                if not alt:
                    return ""
                parts.append(f"{alt[1]}={version(alt[0])}")
            except PackageNotFoundError:
                return ""
    return ";".join(parts)
