"""push 문장 읽기의 기본값은 main 이다: 문장의 모든 낱말이 해롭지 않다고 알려진 것일 때만 push_tag 걸림을 거둔다.

세 시험이 이를 본다. (1) Checker 가 찾은 네 갈래와 덧붙인 메모의 글은 모두 main 과 같이 걸린다(표).
(2) Checker 의 생성기를 옮긴 2만 개 출력에서 main 이 걸고 이 게이트가 통과시킨 것은, 시험이 따로 쓴 단순한
분류기가 보기에도 대상이 모두 작업 브랜치이고 남는 낱말이 없다. (3) 통과하는 문장에 낱말 하나를 끼운 5만 개에서
여전히 통과하면 끼운 낱말은 게이트가 공개한 닫힌 목록에 있다(모르는 낱말은 main 대로).
분류기와 그 목록은 이 파일의 것이다. 게이트의 코드를 가져다 판정하지 않는다.
"""
from __future__ import annotations

import random
import re

import pytest

from output_gate_corpus_record import PROPS
from output_gate_main_reference import main_person_only
from vinc_client import output_gate
from vinc_client.output_gate import OutputGate

GATE = OutputGate(PROPS)
ACTIONS = tuple(p["action"] for p in PROPS["person_only"])


def _verdicts(text: str) -> tuple[set[str], set[str]]:
    got = GATE.evaluate(text)
    mine = set(got.person_only_actions) if "person_only" in got.failed_checks else set()
    return mine, set(main_person_only(text, ACTIONS))


# ---- 시험의 분류기. 게이트와 따로 쓴 규칙이다: 접두사 목록, 버전 꼴, 보호 이름, 해롭지 않은 낱말.
_PREFIXES = {"fix", "feat", "feature", "chore", "docs", "test", "tests", "refactor", "perf", "security", "ci",
             "build", "style", "wip", "spike", "dependabot", "users", "weg1456", "agents", "design", "platform",
             "vincos", "infra", "revert"}
_GUARDED = ("main", "master", "trunk", "head", "production", "prod", "release", "releases", "gh-pages", "stable",
            "develop", "default", "live", "staging", "deploy", "latest", "nightly")
_VERSIONISH = re.compile(r"v\d|\d+[._-]\d+|^\d+$|\d{6}|(?:release|rel|rc|alpha|beta|snapshot|nightly|latest|stable"
                         r"|hotfix|pre|preview)[._-]?\d|\d[._-]?(?:release|rel|rc|alpha|beta|snapshot|nightly"
                         r"|latest|stable|hotfix|pre|preview)", re.I)
_HARMLESS_FLAGS = {"-u", "--set-upstream", "--force-with-lease", "--force-if-includes", "-f", "--force",
                   "--no-verify", "--quiet", "-q", "--verbose", "-v", "--dry-run", "-n"}
_HARMLESS_WORDS = {
    "i", "i'll", "i’ll", "i'd", "i’d", "we", "we'll", "we’ll", "will", "would", "let", "me", "am", "going", "plan",
    "please", "now", "then", "and", "to", "onto", "with", "again", "first", "next", "up", "just", "also", "is", "was",
    "has", "been", "be", "are", "you", "when", "ready", "the", "a", "my", "our", "this", "branch", "working", "work",
    "feature", "topic", "of", "git", "run", "ran", "push", "pushed", "pushes", "pushing", "force-push",
    "force-pushed", "force-pushing", "open", "opened", "opening", "opens", "create", "created", "draft", "pull",
    "request", "pr", "review", "requested", "for", "on", "step", "origin", "upstream", "remote",
    "브랜치", "를", "을", "에", "로", "으로", "는", "은", "그리고", "와", "과", "제가", "하고", "하겠습니다", "했습니다", "합니다",
    "할게요", "함", "한", "했고", "하였습니다", "해", "푸시", "열었습니다", "열겠습니다", "열고", "다음", "단계",
}


def _ordinary(name: str, introduced: bool) -> bool:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*(?:/[A-Za-z0-9][A-Za-z0-9._-]*)*", name) or name.endswith("."):
        return False
    low = name.lower()
    parts = low.split("/")
    if any(p in _GUARDED for p in parts) or low.startswith(_GUARDED) or any(_VERSIONISH.search(p) for p in parts):
        return False
    if len(parts) >= 2 and parts[0] in _PREFIXES:
        return parts[0] != "users" or len(parts) >= 3
    return introduced and any(c in name for c in "/-_")


def _words(sentence: str) -> list[str]:
    out = []
    for piece in re.split(r"[\s`\"“”‘()|]+", sentence):
        piece = piece.lstrip("'’").rstrip("'’.,;!:")
        # 한글과 영숫자가 붙어 있으면 나눈다("fix/x를").
        out += [p for p in re.findall(r"[가-힣]+|[^가-힣]+", piece) if p]
    return [p for p in out if p]


def _leftover(sentence: str, targets: list[str]) -> list[str]:
    """대상·해롭지 않은 플래그·원격·PR 번호를 지우고 해롭지 않은 낱말 목록에도 없는 낱말. 다른 플래그는 여기 남는다."""
    names = {t.lower() for t in targets}
    left = []
    for word in _words(sentence):
        low = word.lower()
        if low in names or low in _HARMLESS_FLAGS or low in _HARMLESS_WORDS or re.fullmatch(r"#\d+", word):
            continue
        # 푸시 뒤의 어미("푸시했습니다")는 어미만 본다.
        if low.startswith("푸시") and low[2:] in _HARMLESS_WORDS | {""}:
            continue
        if low.startswith("브랜치") and low[3:] in _HARMLESS_WORDS | {""}:
            continue
        left.append(word)
    return left


def _introduced(sentence: str, target: str) -> bool:
    t = re.escape(target)
    return bool(re.search(rf"(?:branch|브랜치):?\s+[`'\"“‘(]?{t}|{t}[`'\"”’)]?\s+(?:branch|브랜치)", sentence, re.I))


# ---- (1) 표: Checker 가 막은 네 갈래와 덧붙인 메모. 하나하나 main 과 같이 걸린다.
_CLASS_1 = ["v1.2.3-rc1", "v2.0.0-beta", "1.2.0-rc.1", "hotfix-1.2.3", "release-1.2", "release_1.2", "rel-1.2",
            "v1-2-3", "2026.11.13-snapshot", "v1.2.3-fix", "1.2-hotfix", "rc-1.2", "release-2026.11", "2.0.0-rc.2",
            "v10.0.0-alpha"]
BLOCKING = (
    [f"Please push the branch {n}." for n in _CLASS_1]
    + [f"I am going to push branch {n}." for n in _CLASS_1]
    + [f"I'll push my branch '{n}' to origin." for n in _CLASS_1]
    + ["Please run `git push origin fix/x nightly`.", "Please run `git push origin fix/x latest`.",
       "Please run `git push origin fix/x foo`.", "Please run `git push origin fix/x it`.",
       "I'll push fix/x and the other branch.", "I'll push fix/x and the base branch.",
       "I'll push fix/x, then the current one.", "Please run `git push origin fix/x @`.",
       "Please run `git push origin feature/production-flag it`", "I will push `fix/x.y`, then `the fix`!",
       "I will push 'users/alice/fix-1', then 'the changes'.", "I'll push fix/x and the branch.",
       "I'll push fix/x and this.", "I'll push fix/x then push.", "Please push fix/x and it.",
       "Let me push fix/x -d.", "Let me push fix/x -d origin.", "Let me push fix/x --delete.",
       "Please push fix/x to 프로덕션.", "Please push fix/x to 릴리스.", "Please push fix/x to 운영.",
       "Please push fix/x to 메인.", "Please push fix/x to 배포 브랜치.",
       "Please push the branch main-fix.", "Please push the branch release-notes.",
       "Please push the branch hotfix/main.", "Please push hotfix/main."]
)
# 문장 나누기가 대상을 다음 문장으로 떼어 낸 꼴. 같은 문단의 앞뒤 글은 모르는 글이라 main 대로 둔다.
BOUNDARY = ["Please run `git push origin fix/main-menu. v1.2.3-rc1.`!", "I'll push fix/x. Also main.",
            "I'll push fix/x and\nv1.2.3.", "Please push fix/x.\nHEAD too.", "Please push fix/x. 메인.",
            "Please push fix/x! And the tag.", "Main and v1.2.3. Please push fix/x.",
            "Please push fix/x and\n- main", "Main and v1.2.3 then # " + "w" * 297 + "\nPlease push fix/x.",
            "Please push fix/x " + "and " * 200 + "."]
NOTES = ["Please push fix/x to heroku.", "Please push fix/x to the deploy branch.", "Please push fix/x to main2.",
         "Please push fix/x to mainline.", "Please push fix/x, then deploy.", "Please push fix/x as the new default.",
         "Please push fix/x and ｍａｉｎ.", "Please run `git push origin fix/x:`.", "Please push fix/x*.",
         "Please push `fix/x*`.", "I will run git push --force-with-lease=main origin fix/x.",
         "I will run git push --force-with-lease=main:abc123 origin wip/stable-sort.",
         "Let me push up fix/develop-docs --force-with-lease=main."]


@pytest.mark.parametrize("text", BLOCKING + NOTES + BOUNDARY)
def test_each_checker_text_does_what_main_does(text):
    mine, main = _verdicts(text)
    assert "push_tag" in main, text
    assert mine == main, text


# 같은 꼴에서 대상만 작업 브랜치이면 거둔다(표가 무엇이든 거는 게이트로는 통과하지 못하게).
CONTROLS = ["Please push the branch fix/main-menu.", "Please push fix/x to origin.",
            "Please run `git push -u origin fix/x`.", "Let me push fix/x --force-with-lease.",
            "I'll push feature/production-flag and open a draft pull request.",
            "브랜치 `fix/x` 를 push 하겠습니다.", "Please push the branch fix-login-copy.",
            "I am going to push branch `fix/x`!", "Please push fix/x.\n\nTests are in CI.",
            "## Next step\nPlease push fix/x.", "Summary:\n- Please push fix/x.\n- Nothing else."]


@pytest.mark.parametrize("text", CONTROLS)
def test_the_same_shape_with_a_working_branch_is_withdrawn(text):
    mine, main = _verdicts(text)
    assert main == {"push_tag"} and mine == set(), text


# ---- (2) Checker 의 생성기(seed 1301)를 옮긴 것. 표지는 생성기가 붙이지만 단언은 시험의 분류기가 한다.
_ORD = ["fix/x", "feat/release-notes", "fix/tag-push-guard", "fix/v2-parser", "security/sync-push-keeps-hub-run-state",
        "fix/community-unified-space-lookup-errors", "fix/main-menu", "feature/production-flag", "docs/readme_update",
        "users/alice/fix-1", "chore/bump-deps", "Fix/X", "fix/x.y", "fix/foo.bar-baz", "feat/merge-queue-ui",
        "fix/send-retry", "fix/publish-flow", "wip/stable-sort", "fix/develop-docs", "refactor/head-parser",
        "fix/release-1.2-notes", "topic/gh-pages-links", "fix/prod-config-loader", "agent/1301-output-gate"]
_ORD_BARE = ["main-fix", "release-notes", "master-backup", "fix-x", "hotfix_login", "production-flag", "develop-docs",
             "stable-sort", "my_branch"]
_PROT = ["main", "master", "trunk", "HEAD", "production", "prod", "release", "release/1.2", "gh-pages", "stable",
         "develop", "MAIN", "Master", "Develop", "STABLE", "origin/main", "origin/master", "HEAD:main", "fix/x:main",
         "fix/x:refs/heads/main", "HEAD:refs/heads/release/1.2", "refs/heads/main", "fix/x:production",
         "fix/x:gh-pages", "+refs/heads/*", "refs/tags/v1", "refs/tags/fix-x", "v1.2.3", "1.2", "v1", "v2.0",
         "v1.2.3-rc1", "1.2.0-beta.1", "v10.0.0-alpha", "2.0.0-rc.2", "release-1.2", "release_1.2", "release-2026.11",
         "releases/2026", "prod/x", "users/master/x", "hotfix/main", "feature/main", "main~1", "HEAD~1", "@",
         "fix/x:v1.2.3", "fix/x:refs/tags/v1", "main:main", "master:fix/x", "rc-1.2", "1.2-hotfix", "v1.2.3-fix",
         "fix/x:release", "fix/x:stable", "fix/x:develop", "fix/x:trunk", "fix/x:HEAD"]
_UNREAD = ["it", "the branch", "the changes", "everything", "both branches", "all branches", "the fix", "my work",
           "fix/한글", "수정/브랜치", "fix/x*", "fix/x?", "the default branch", "the protected branch", "the tag"]
_SAFE_FLAGS = ["", "-u", "--force-with-lease", "--force", "-f", "-u origin", "--set-upstream origin", "--no-verify",
               "--force-with-lease origin", "--atomic", "--dry-run", "-v", "--quiet"]
_BAD_FLAGS = ["--tags", "--follow-tags", "--all", "--mirror", "-d", "--delete", "--prune", "--force-with-lease=main",
              "--force-with-lease=main:abc123", "--push-option=release"]
_WRAP = [lambda t: t, lambda t: f"`{t}`", lambda t: f"'{t}'", lambda t: f'"{t}"', lambda t: f"({t})",
         lambda t: f"**{t}**", lambda t: f"“{t}”", lambda t: f"[{t}](https://github.com/Vinculums/Vinc/tree/{t})",
         lambda t: f"{t}.", lambda t: f"{t},", lambda t: f"{t};", lambda t: f"{t}!", lambda t: f"{t}:"]
_REMOTES = ["origin", "upstream", "heroku", "production", "github", "fork", "staging", "live"]
_EN_PUSH = [
    "I'll push {T} and open a draft pull request", "Let me push {T} to {R}", "I will run git push {F} {R} {T} next",
    "Next step: please push {T}", "Please push the branch {T} when you are ready", "Pushing {T} now",
    "Push {T} to {R} and request review", "I'd push {T} {F} after the tests", "Awaiting your approval to push {T}",
    "We'll push {T} upstream", "Please push to {T}", "Recommend pushing {T} ({F})", "I am going to push branch {T}",
    "You should push {T} {F}", "I plan to force-push {T}", "Push: {T}", "Once approved, I'll push {F} {T}",
    "I'll `git push {F} {R} {T}` and wait", "Needs your sign-off before I push {T}", "Let me push up {T} {F}",
    "I'll push my branch {T} to {R}", "Please push working branch {T} onto {R}", "push {T} please",
    "I will push {T}, then {T2}", "I'll push {T} and push {T2}", "I'll push {T} and {T2}",
    "Please run `git push {R} {T} {T2}`", "I'll push {T} {F} and then push {T2} {F2}",
    "Branch {T} is ready; I'll push it", "I'll push it: {T}", "{T} (push pending, please approve)",
    "Branch {T}: please push", "I'll push {T} to the {R} remote", "Please git push {T}",
]
_KO_PUSH = [
    "브랜치 {T} 를 푸시하겠습니다", "{T} 브랜치를 push 하겠습니다", "제가 {T} 를 git push 하겠습니다",
    "푸시할 브랜치: {T}, 승인 부탁드립니다", "{T} 를 {R} 에 push 해 주세요", "승인되면 git push -u {R} {T} 를 실행하겠습니다",
    "{T} 브랜치 push 승인이 필요합니다", "브랜치 {T} 를 {F} 로 push 하겠습니다", "제가 {T} 와 {T2} 를 push 하겠습니다",
    "| 브랜치 | {T} | push 승인 필요 |", "- {T} push 대기 (승인 필요)", "{T} push 하고 draft PR 을 열겠습니다",
    "다음 단계: {T} 브랜치 푸시를 승인해 주세요 (git push)", "결정 필요: {T} 를 push 할까요",
]
_OTHER = {
    "tag": ["tag v1.2.3", "push the tag v1.2.3", "cut the 1.2 release tag", "태그 v1.2.3 를 push", "push --tags"],
    "merge": ["merge it", "merge #1301", "병합"], "publish": ["publish the release notes", "publish it"],
    "send": ["send the summary to the team", "send the email"],
    "migration": ["run the migration on live data", "마이그레이션 실행"],
}
_JOIN_SAME = ["{A} and {B}", "{A}, then {B}", "{A}; please {B}", "{A}, and I'll {B}", "{A} 후 {B} 하겠습니다"]
_NEIGH = {"tag": ["Please tag v1.2.3 after that.", "I'll push the tag v1.2.3 next.", "태그 v1.2.3 push 도 승인해 주세요."],
          "merge": ["Please merge it after review.", "I'll merge #1301 once CI is green.", "병합을 승인해 주세요."],
          "publish": ["Please publish the release notes.", "I'll publish the docs."],
          "send": ["Please send the summary to the team.", "I'll send the email."],
          "migration": ["Please run the migration on live data.", "I'll run the migration."]}
_NEG = ["I did not push {T}; please push {T} when ready.", "I will not push {T}.", "No tag was pushed; please push {T}.",
        "I have not pushed {T} yet, please approve the push.", "Not pushing {T} until you approve.",
        "{T} 는 아직 push 하지 않았습니다. 승인해 주세요.", "Nothing was pushed to {T}; I'll push {T} after approval."]
_NONPUSH = ["The push-tag guard in {T} reads tags; please review.", "Please review {T}; it touches the push handler.",
            "Please review the tag parser in {T}.", "I'll fix the push notification bug in {T}.",
            "Recommend renaming {T}; tag names are confusing.", "Please look at the stage tagging code.",
            "푸시 알림 버그를 {T} 에서 고치겠습니다.", "Push notifications: please approve the copy in {T}."]
_FILLER = ["Tests: 412 passed in the pull request checks.", "The draft pull request is #1301.", "",
           "Summary below.", "See the table.", "작업 요약입니다.", "Nothing was merged."]


def _pick(rng: random.Random) -> str:
    r = rng.random()
    if r < .35:
        return rng.choice(_ORD)
    if r < .45:
        return rng.choice(_ORD_BARE)
    if r < .85:
        return rng.choice(_PROT)
    return rng.choice(_UNREAD)


def _build(rng: random.Random) -> tuple[str, dict]:
    t, t2 = _pick(rng), _pick(rng)
    w = rng.choice(_WRAP)
    f = rng.choice(_SAFE_FLAGS + _BAD_FLAGS) if rng.random() < .5 else ""
    f2 = rng.choice(_SAFE_FLAGS + [""] * 4)
    rem = rng.choice(_REMOTES) if rng.random() < .4 else "origin"
    lab = {"targets": [t], "flags": [f, f2], "remote": rem, "other": [], "kind": "push"}
    kind = rng.random()
    if kind < .62:
        tpl = rng.choice(_EN_PUSH if rng.random() < .7 else _KO_PUSH)
    elif kind < .75:
        tpl = rng.choice(_NEG)
        lab["kind"] = "neg"
    elif kind < .85:
        tpl = rng.choice(_NONPUSH)
        lab["kind"] = "nonpush"
    else:
        tpl = rng.choice(_EN_PUSH + _KO_PUSH)
        o = rng.choice(list(_OTHER))
        lab["other"].append(o)
        tpl = rng.choice(_JOIN_SAME).replace("{A}", tpl).replace("{B}", rng.choice(_OTHER[o]))
    if "{T2}" in tpl:
        lab["targets"].append(t2)
    if "{R}" not in tpl:
        lab["remote"] = "origin"
    s = tpl.replace("{T2}", w(t2)).replace("{T}", w(t)).replace("{F2}", f2).replace("{F}", f).replace("{R}", rem)
    s = " ".join(s.split())
    if not s.endswith(("|", ".", "!", "?")):
        s += rng.choice([".", ".", "!", "", " (draft PR #1301)."])
    lab["sentence"] = s
    parts = [s]
    if rng.random() < .3:
        o = rng.choice(list(_NEIGH))
        lab["other"].append(o)
        parts.insert(rng.choice([0, 1]), rng.choice(_NEIGH[o]))
    if rng.random() < .5:
        parts.insert(rng.randrange(len(parts) + 1), rng.choice(_FILLER))
    sep = rng.choice([" ", "\n", "\n- ", "\n\n"])
    return sep.join(p for p in parts if p), lab


def _checker_outputs(n: int = 20_000, seed: int = 1301) -> list[tuple[str, dict]]:
    rng, seen, out = random.Random(seed), set(), []
    while len(out) < n:
        text, lab = _build(rng)
        if text not in seen:
            seen.add(text)
            out.append((text, lab))
    return out


def test_the_checker_generator_keeps_p1_and_every_withdrawal_is_ordinary_with_nothing_left_over():
    rows = _checker_outputs()
    assert len(rows) >= 20_000
    withdrawn = 0
    for text, lab in rows:
        mine, main = _verdicts(text)
        assert mine <= main, (text, mine, main)
        assert main - mine <= {"push_tag"}, (text, mine, main)
        if main and not mine:
            withdrawn += 1
            sentence = lab["sentence"]
            assert lab["kind"] == "push" and not lab["other"], text
            assert all(_ordinary(t, _introduced(sentence, t)) for t in lab["targets"]), text
            assert not _leftover(sentence, lab["targets"]), (text, _leftover(sentence, lab["targets"]))
    # 생성기가 거두는 쪽을 실제로 건드리는지.
    assert withdrawn >= 250, withdrawn


# ---- (3) 퍼즈: 통과하는 push 문장에 낱말 하나를 끼운다. 모르는 낱말이면 main 대로 걸린다.
_BASE_TEMPLATES = ["I'll push {n} and open a draft pull request.", "Please push the branch {n}.",
                   "Let me push {n} to origin.", "Please run `git push -u origin {n}`.",
                   "I will push {n} --force-with-lease now.", "Next step: push the working branch {n}.",
                   "Please push {n} to origin again.", "Let me push the branch {n} and request review.",
                   "브랜치 `{n}` 를 push 하겠습니다.", "{n} 브랜치를 push 하겠습니다.", "Please push {n} and {m}."]
_BASE_NAMES = ["fix/x", "feat/login-copy", "security/sync-push-keeps-hub-run-state", "docs/readme_update",
               "fix/main-menu", "users/alice/fix-1", "chore/bump-deps"]
_FUZZ_WORDS = (
    ["it", "this", "that", "them", "one", "nightly", "latest", "foo", "bar", "main", "master", "HEAD", "head", "trunk",
     "develop", "stable", "release", "prod", "production", "deploy", "heroku", "default", "other", "current", "base",
     "everything", "all", "both", "tags", "tag", "branches", "changes", "fix", "work", "mainline", "main2", "live",
     "staging", "gh-pages", "upstream", "origin", "fork", "the", "a", "and", "then", "to", "with", "branch", "now",
     "again", "please", "draft", "pull", "request", "review", "open", "opened", "git", "push", "pushed", "as", "new",
     "into", "over", "onto", "after", "before", "tests", "merge", "publish", "send", "remote", "my", "our", "your"]
    + ["-d", "--delete", "--tags", "--follow-tags", "--all", "--mirror", "--prune", "-u", "--set-upstream", "-f",
       "--force", "--force-with-lease", "--force-with-lease=main", "--no-verify", "-q", "--quiet", "-v", "--dry-run",
       "-n", "--atomic", "-o", "--push-option=release", "--receive-pack=x", "--repo=main", "-4"]
    + ["@", ":", "+", "*", "~", "^", "@{u}", "HEAD~1", ":main", "+main", "*", "->", "=>", "&", "#", "--", "=", "/",
       "|", ",", ";", ".", "!", "?", "…", "(", ")", "[x]", "<main>", "refs/tags/v1", "refs/heads/main", "HEAD:main"]
    + ["프로덕션", "릴리스", "배포", "운영", "메인", "기본", "태그", "모두", "전부", "를", "에", "로", "그리고", "브랜치",
       "다른", "현재", "ｍａｉｎ", "ｖ１", "мain", "maín"]
    + ["v1.2.3", "1.2", "v2", "2026.11.13", "rc1", "v1-2-3", "1.2.0-rc.1", "release-1.2", "1.2-hotfix", "v10",
       "2.0.0", "fix/v1.2", "fix/x-v2", "fix/main", "release/1.2", "hotfix/main", "fix/y", "feat/z", "fix-y"]
)


def _fuzz_token(rng: random.Random) -> str:
    if rng.random() < .1:
        # 목록에 없는 낱말도 섞는다.
        alphabet = "abcdefghijklmnopqrstuvwxyz0123456789-_./:@"
        return "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 8)))
    return rng.choice(_FUZZ_WORDS)


def _base_sentences() -> list[str]:
    out = []
    for tpl in _BASE_TEMPLATES:
        for name in _BASE_NAMES:
            sentence = tpl.format(n=name, m="fix/second-branch")
            mine, main = _verdicts(sentence)
            if main == {"push_tag"} and not mine:
                out.append(sentence)
    return out


def _closed(token: str) -> bool:
    """끼운 낱말이 게이트가 공개한 닫힌 목록 가운데 하나이거나, 시험의 분류기가 작업 브랜치로 읽는 이름인가."""
    word = token.strip("`'\"“”‘’()|").strip("'’").rstrip(".,;!:")
    if not word:
        return True
    lists = output_gate.PUSH_READING_LISTS
    low = word.lower()
    return (any(low in values for values in lists.values()) or _ordinary(word, True)
            or (low.startswith("푸시") and low[2:] in lists["korean_endings"]))


def test_one_inserted_unknown_token_does_what_main_does():
    rng = random.Random(20261113)
    bases = _base_sentences()
    assert len(bases) >= 60, len(bases)
    checked = still_withdrawn = 0
    while checked < 50_000:
        base = rng.choice(bases)
        token = _fuzz_token(rng)
        words = base.split(" ")
        at = rng.randrange(len(words) + 1)
        glued = ""
        if rng.random() < .2 and at < len(words):
            # 이웃 낱말에 붙여 끼운다("fix/x@", "-d."). 붙은 낱말 전체도 본다.
            words[at] = glued = words[at] + token if rng.random() < .5 else token + words[at]
        else:
            words.insert(at, token)
        text = " ".join(words)
        mine, main = _verdicts(text)
        checked += 1
        assert mine <= main and main - mine <= {"push_tag"}, (text, mine, main)
        if main and not mine:
            still_withdrawn += 1
            assert _closed(token) or (glued and _closed(glued)), (token, text)
    assert still_withdrawn >= 1000, still_withdrawn


# ---- 다른 덩어리가 지키는 대상을 싣는 글. 거두는 문장 하나만 보면 안 된다: 글의 나머지가 조용해야 한다.
_LATER_BLOCK_TEXTS = [
    "I'll push fix/x.\n\n```\ngit push --tags\n```",
    "I'll push fix/x.\n\n```\ngit push origin main\n```",
    "I'll push fix/x.\n\n```\ngit push origin v1.2.3\n```",
    "I'll push fix/x.\n\n```bash\ngit tag v1.2.3 && git push --tags\n```",
    "I'll push fix/x.\n\n`git push origin main`",
    "I'll push fix/x.\n\n    git push origin main",
    "I'll push fix/x.\n## Then\n    git push origin main",
    "- I'll push fix/x.\n- Then tag it v1.2.3.",
    "I'll push fix/x.\n\nThen tag it v1.2.3.",
    "I'll push fix/x.\n\nIt goes to main.",
    "- I'll push fix/x.\n- to main.",
    "I'll push fix/x.\n- 태그 v1.2.3 도 같이.",
    "I'll push fix/x.\n\n- 메인에도.",
    "I'll push fix/x.\n\n- with --tags",
]


@pytest.mark.parametrize("text", _LATER_BLOCK_TEXTS)
def test_a_later_block_with_a_guarded_target_keeps_what_main_does(text):
    mine, main = _verdicts(text)
    assert "push_tag" in main, text
    assert mine == main, text


_PUSH_FIRST = ["I'll push fix/x.", "I will push the branch fix/login-form.", "Please push feat/feed-home.",
               "I'll push the working branch fix/x.", "I'll push `docs/readme-typo`.",
               "Let me push fix/x to origin."]
_SEPARATORS = ["\n\n", "\n- ", "\n\n- ", "\n## Then\n", "\n\n```\n", "\n\n    "]
_SECOND = ["git push --tags", "git push origin main", "git push origin v1.2.3", "Then tag it v1.2.3.",
           "It goes to main.", "to main.", "with --tags", "태그 v1.2.3 도 같이.", "메인에도.", "운영에 배포.",
           "and the release branch.", "also master.", "HEAD too.", "then gh-pages.", "push it to production.",
           "refs/tags/v2 as well.", "@ as well."]
_QUIET_SECOND = ["The tests are green.", "Draft pull request #12 is open.", "테스트는 통과했습니다.",
                 "Nothing else changed."]


def test_two_blocks_keep_what_main_does_and_a_quiet_second_block_is_still_withdrawn():
    kept = withdrawn = 0
    for first in _PUSH_FIRST:
        alone_mine, alone_main = _verdicts(first)
        assert "push_tag" in alone_main and "push_tag" not in alone_mine, first
        for separator in _SEPARATORS:
            for second in _SECOND:
                text = first + separator + second + ("\n```" if "```" in separator else "")
                mine, main = _verdicts(text)
                # 새로 거는 것은 없다(P1). main 이 건 push_tag 는 그대로 걸린다.
                assert mine <= main, text
                assert ("push_tag" in main) == ("push_tag" in mine), text
                kept += "push_tag" in mine
            for second in _QUIET_SECOND:
                text = first + separator + second + ("\n```" if "```" in separator else "")
                mine, main = _verdicts(text)
                assert mine <= main, text
                withdrawn += "push_tag" in main and "push_tag" not in mine
    assert kept >= 500, kept
    # 조용한 둘째 덩어리는 거두는 것을 막지 않는다(빈 줄이나 새 목록 줄로 나뉜 경우).
    assert withdrawn >= 20, withdrawn


def test_many_withdrawable_push_paragraphs_take_linear_time():
    import time
    text = "\n\n".join(f"I'll push fix/x{i}." for i in range(4000))
    started = time.perf_counter()
    GATE.evaluate(text)
    assert time.perf_counter() - started < 5.0
