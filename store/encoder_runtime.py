"""본문·질의 벡터를 만드는 세 패키지의 실제 판을 한 지문으로 묶는다."""

from importlib.metadata import PackageNotFoundError, version


# 각 항목: (배포판 이름들, 라벨). GPU 판(onnxruntime-gpu)은 같은 모듈을 다른 배포판 이름으로
# 싣는다 — 라벨을 갈라 둔다(CUDA 와 CPU 의 fp32 결과는 아주 조금 다르므로 같은 지문이면 안 된다).
_PACKAGES = (((("onnxruntime", "ort"), ("onnxruntime-gpu", "ort-gpu"))),
             ((("tokenizers", "tok"),)), ((("numpy", "np"),)))


def fingerprint() -> str:
    """세 판이 모두 있을 때만 지문을 낸다. 하나라도 없으면 비교 가능한 척하지 않는다.

    호스티드의 사이드카와 인코더는 같은 이미지 SHA 로 뜬다.
    """
    parts = []
    for choices in _PACKAGES:
        for package, label in choices:
            try:
                parts.append(f"{label}={version(package)}")
                break
            except PackageNotFoundError:
                continue
        else:
            return ""
    return ";".join(parts)
