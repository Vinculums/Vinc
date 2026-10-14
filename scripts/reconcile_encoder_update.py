"""갱신 응답이 불확실할 때 실제 리비전을 읽어 확인한다. 갱신은 재전송하지 않는다."""
from __future__ import annotations

import argparse
import json
import subprocess
import time
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.verify_current_hub import ready


def update(image, *, run=subprocess.run, sleep=time.sleep, attempts=6):
    command=["az","containerapp","update","--name","vinc-encode","--resource-group","vincsRG",
             "--image",image,"--min-replicas","1","--output","none"]
    result=run(command,capture_output=True,text=True)
    if result.returncode==0:
        print("encoder update acknowledged")
        return
    print("encoder acknowledgement uncertain; reading actual desired state")
    for attempt in range(attempts):
        result=run(["az","containerapp","show","-n","vinc-encode","-g","vincsRG","-o","json"],
                   capture_output=True,text=True)
        try:
            app=json.loads(result.stdout) if result.returncode==0 else {}
            minimum=app.get("properties",{}).get("template",{}).get("scale",{}).get("minReplicas")
            if ready(app,image) and type(minimum) is int and minimum>=1:
                revision=app["properties"]["latestRevisionName"]
                state=run(["az","containerapp","revision","show","-n","vinc-encode","-g","vincsRG",
                           "--revision",revision,"--query","properties.runningState","-o","tsv"],
                          capture_output=True,text=True)
                if state.returncode==0 and state.stdout.strip() in ("Running","RunningAtMaxScale"):
                    print("encoder update verified despite missing acknowledgement")
                    return
        except (ValueError,TypeError):
            pass
        if attempt+1<attempts:
            sleep(5)
    raise RuntimeError("encoder update is not verified; preserve failed deployment")


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--image",required=True)
    update(parser.parse_args().image)
