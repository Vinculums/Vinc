"""갱신 응답이 불확실할 때 실제 리비전을 읽어 확인한다. 갱신은 재전송하지 않는다."""
from __future__ import annotations

import argparse
import json
import subprocess
import time

try:
    from scripts._root import ROOT  # noqa: F401
except ImportError:  # 직접 실행: 리포 루트가 아직 sys.path 에 없다
    from _root import ROOT  # noqa: F401
from scripts.verify_current_hub import ready


def update(image, *, min_replicas=1, run=subprocess.run, sleep=time.sleep, attempts=6):
    # min_replicas 0 이면 아무도 부르지 않을 때 레플리카가 0 이라 실행 상태를 확인할 대상이 없다.
    command=["az","containerapp","update","--name","vinc-encode","--resource-group","vincsRG",
             "--image",image,"--min-replicas",str(min_replicas),"--output","none"]
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
            if ready(app,image) and type(minimum) is int and minimum==min_replicas:
                if min_replicas==0:
                    print("encoder update verified despite missing acknowledgement (scaled to zero)")
                    return
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
    parser.add_argument("--min-replicas",type=int,default=1,choices=(0,1))
    args=parser.parse_args()
    update(args.image,min_replicas=args.min_replicas)
