"""
Health Check — kinmyakucode パイプライン全体の健全性を1コマンドで診断する朝のダッシュボード.

POSTMORTEM_2026-07-05 §4 P0-2 (日次ヘルスレポート) の実装。
「エラーなく終わった」ではなく「動くべきものが動き、出るべきものが出たか」を確認する。

チェック項目:
  1. MOUNT    : pdf_xbrl junction / E: / 正史xbrl_store の存在
  2. TASK     : Windows タスク DailyStockFlowUpdate の状態 (Disabled放置・失敗を検知)
  3. GIT      : 両リポの dirty / ahead / behind (未pushの修正でCIが古いコードで走る事故を検知)
  4. ACTIONS  : GitHub Actions daily-update の直近結果 (gh CLI があれば)
  5. LOGS     : 最新 daily_update ログの鮮度とエラー数
  6. DATA     : xbrl_store / frontend public/data の鮮度
  7. AUDITS   : 最後の yuho_audit 結果 (logs/yuho_audit_last.json)
  8. HYGIENE  : credentials.json の gitignore 漏れ等

使い方:
  python health_check.py            # 標準 (git fetch あり, ネットワーク監査なし) ~20秒
  python health_check.py --fast     # git fetch なし (オフライン/即答)
  python health_check.py --audit    # + tanshin_audit/yuho_audit を実行 (TDnet/EDINETへアクセス)
  python health_check.py --gates    # + 品質ゲート3種 (validate_payout/scan_forbidden/scan_report_quality)
  python health_check.py --full     # 全部
  python health_check.py --json     # 機械可読JSON出力

終了コード: 0=全OK / 1=WARNあり / 2=CRITICALあり
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(os.environ.get("PROJECT_ROOT") or Path(__file__).resolve().parent)
PDF_ROOT = Path(os.environ.get("PDF_ROOT") or (PROJECT_ROOT / "pdf_xbrl"))
XBRL_STORE = Path(os.environ.get("XBRL_STORE") or (PROJECT_ROOT / "financial_analysis_system" / "xbrl_store"))
STOCKFLOW_ROOT = Path(os.environ.get("STOCKFLOW_ROOT") or (PROJECT_ROOT / "StockFlow"))
FRONTEND_DATA = STOCKFLOW_ROOT / "frontend" / "public" / "data"
LOG_DIR = PROJECT_ROOT / "logs"
PYTHON = os.environ.get("PYTHON_EXE") or sys.executable

OK, WARN, CRIT = "OK", "WARN", "CRIT"
_SEV = {OK: 0, WARN: 1, CRIT: 2}


class Report:
    def __init__(self) -> None:
        self.sections: list[dict] = []

    def add(self, section: str, status: str, message: str) -> None:
        self.sections.append({"section": section, "status": status, "message": message})

    @property
    def worst(self) -> int:
        return max((_SEV[s["status"]] for s in self.sections), default=0)


def run(cmd: list[str], cwd: Path | None = None, timeout: int = 60) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, cwd=str(cwd) if cwd else None, capture_output=True,
                           text=True, timeout=timeout, encoding="utf-8", errors="replace")
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired:
        return -1, "(timeout)"
    except FileNotFoundError:
        return -2, "(command not found)"


# ===========================================================================
# 1. MOUNT
# ===========================================================================
def check_mount(rep: Report) -> None:
    if not PDF_ROOT.exists():
        rep.add("MOUNT", CRIT, f"pdf_xbrl が存在しない: {PDF_ROOT} — junction切れ (POSTMORTEM根本原因の再発)。E:ドライブ接続と `mklink /J` を確認")
        return
    years = sorted(p.name for p in PDF_ROOT.glob("20*") if p.is_dir())
    if not years:
        rep.add("MOUNT", CRIT, f"pdf_xbrl 配下に年フォルダが無い (マウント先が空): {PDF_ROOT}")
    else:
        rep.add("MOUNT", OK, f"pdf_xbrl OK (年フォルダ {years[0]}..{years[-1]})")
    if not XBRL_STORE.is_dir():
        rep.add("MOUNT", CRIT, f"正史 xbrl_store が存在しない: {XBRL_STORE}")
    else:
        n = sum(1 for p in XBRL_STORE.iterdir() if p.is_dir())
        rep.add("MOUNT", OK if n > 3000 else WARN, f"正史 xbrl_store: {n:,} 企業フォルダ" + ("" if n > 3000 else " (少なすぎ — store破損の疑い)"))


# ===========================================================================
# 2. TASK (Windows スケジュールタスク)
# ===========================================================================
def check_task(rep: Report) -> None:
    if os.name != "nt":
        rep.add("TASK", OK, "(Windows以外のためスキップ)")
        return
    ps = ("$t = Get-ScheduledTask -TaskName 'DailyStockFlowUpdate' -ErrorAction SilentlyContinue; "
          "if ($t) { $i = Get-ScheduledTaskInfo -TaskName 'DailyStockFlowUpdate'; "
          "@{State=$t.State.ToString(); LastRunTime=$i.LastRunTime.ToString('yyyy-MM-dd HH:mm'); "
          "LastTaskResult=$i.LastTaskResult; NextRunTime=[string]$i.NextRunTime} | ConvertTo-Json } "
          "else { '{}' }")
    rc, out = run(["powershell", "-NoProfile", "-Command", ps], timeout=30)
    try:
        info = json.loads(out.strip() or "{}")
    except json.JSONDecodeError:
        info = {}
    if not info:
        rep.add("TASK", WARN, "DailyStockFlowUpdate タスクが見つからない (削除済み? GitHub Actions単独運用なら想定内)")
        return
    state = info.get("State", "?")
    result = info.get("LastTaskResult", "?")
    last = info.get("LastRunTime", "?")
    line = f"DailyStockFlowUpdate: State={state} LastRun={last} LastResult={result}"
    if state == "Disabled":
        rep.add("TASK", WARN, line + " — Disabledのまま。意図的な無効化なら再有効化の要否を判断 (Actions単独運用に移行するならこのままでOK)")
    elif str(result) not in ("0", "267009", "267011"):  # 0=成功, 267009=実行中, 267011=未実行
        rep.add("TASK", CRIT, line + " — 直近実行が失敗している。logs/scheduler.log を確認")
    else:
        rep.add("TASK", OK, line)


# ===========================================================================
# 3. GIT (両リポ)
# ===========================================================================
def check_git(rep: Report, do_fetch: bool) -> None:
    for label, path, critical_files in (
        ("root(LLM-Project)", PROJECT_ROOT, ("daily_update.py", "yuho_audit.py", "tanshin_audit.py", ".github/workflows/daily-update.yml")),
        ("StockFlow(kinmyakucode)", STOCKFLOW_ROOT, ()),
    ):
        if not (path / ".git").exists():
            rep.add("GIT", WARN, f"{label}: .git が見つからない ({path})")
            continue
        if do_fetch:
            run(["git", "-C", str(path), "fetch", "origin"], timeout=45)
        rc, porcelain = run(["git", "-C", str(path), "status", "--porcelain"])
        dirty = [l for l in porcelain.splitlines() if l.strip()]
        modified = [l[3:] for l in dirty if l.startswith((" M", "M "))]
        rc, counts = run(["git", "-C", str(path), "rev-list", "--left-right", "--count", "origin/main...main"])
        behind = ahead = 0
        m = re.match(r"\s*(\d+)\s+(\d+)", counts)
        if m:
            behind, ahead = int(m.group(1)), int(m.group(2))
        msg = f"{label}: 変更{len(dirty)}件 / origin比 ahead {ahead}, behind {behind}"
        crit_mod = [f for f in critical_files if any(f in mm for mm in modified)]
        if ahead > 0 and label.startswith("root"):
            rep.add("GIT", WARN, msg + f" — 未pushコミット{ahead}件。CI (GitHub Actions) は origin の古いコードで毎朝走っている。push承認を検討")
        elif behind > 0:
            rep.add("GIT", WARN, msg + " — origin が先行。pull を検討 (Actionsのbot commitの可能性)")
        else:
            rep.add("GIT", OK if not crit_mod else WARN, msg + (f" — 基幹ファイル未コミット: {', '.join(crit_mod)}" if crit_mod else ""))
        if label.startswith("StockFlow") and dirty:
            rep.add("GIT", WARN, f"StockFlow に未コミット変更 {len(dirty)}件 — 06:00 の自動commit+pushが作業中状態を拾う危険 (feedback-local-daily-task-autopush)")


# ===========================================================================
# 4. GitHub Actions
# ===========================================================================
def check_actions(rep: Report) -> None:
    gh = shutil.which("gh")
    if not gh:
        rep.add("ACTIONS", WARN, "gh CLI 未導入のため Actions の成否を確認できない → `winget install GitHub.cli` 後 `gh auth login` (クラウド日次の死活監視に必須)")
        return
    rc, out = run([gh, "run", "list", "--repo", "fGafarm/LLM-Project", "--workflow", "daily-update.yml",
                   "--limit", "3", "--json", "conclusion,status,createdAt"], timeout=45)
    if rc != 0:
        rep.add("ACTIONS", WARN, f"gh run list 失敗 (認証切れ?): {out.strip()[:120]}")
        return
    try:
        runs = json.loads(out)
    except json.JSONDecodeError:
        rep.add("ACTIONS", WARN, "gh の出力を解析できない")
        return
    if not runs:
        rep.add("ACTIONS", WARN, "daily-update.yml の実行履歴が無い (cron未発火/ワークフロー無効)")
        return
    latest = runs[0]
    concl = latest.get("conclusion") or latest.get("status")
    when = (latest.get("createdAt") or "")[:16].replace("T", " ")
    fails = sum(1 for r in runs if r.get("conclusion") == "failure")
    if concl == "success":
        rep.add("ACTIONS", OK, f"直近run success ({when} UTC), 直近3回中失敗{fails}")
    else:
        rep.add("ACTIONS", CRIT, f"直近run = {concl} ({when} UTC) — クラウド日次が失敗中。gh run view --log で原因確認")


# ===========================================================================
# 5. LOGS
# ===========================================================================
def check_logs(rep: Report) -> None:
    logs = sorted(LOG_DIR.glob("daily_update_*.log"))
    if not logs:
        rep.add("LOGS", WARN, f"daily_update ログが無い: {LOG_DIR}")
        return
    latest = logs[-1]
    m = re.search(r"(\d{8})", latest.name)
    log_date = datetime.strptime(m.group(1), "%Y%m%d") if m else None
    age_days = (datetime.now() - log_date).days if log_date else 999
    text = latest.read_text(encoding="utf-8", errors="replace")
    n_err = text.count("[ERROR]")
    complete = "Pipeline complete" in text
    msg = f"最新ログ {latest.name} (対象日から{age_days}日経過, ERROR {n_err}件, complete={'有' if complete else '無'})"
    if age_days > 3:
        rep.add("LOGS", CRIT, msg + " — 3日以上パイプラインが走っていない (ローカル・手動とも)。サイレント死の疑い")
    elif n_err > 0 or not complete:
        rep.add("LOGS", WARN, msg + f" — `python -c` 等で {latest.name} の ERROR 行を確認")
    else:
        rep.add("LOGS", OK, msg)


# ===========================================================================
# 6. DATA freshness
# ===========================================================================
def check_data(rep: Report) -> None:
    now = datetime.now()
    if XBRL_STORE.is_dir():
        newest = 0.0
        for d in XBRL_STORE.iterdir():
            try:
                mt = d.stat().st_mtime
                if mt > newest:
                    newest = mt
            except OSError:
                continue
        age_h = (now - datetime.fromtimestamp(newest)).total_seconds() / 3600 if newest else 9999
        # 週末は提出が無いので3日 (72h+α) までは正常扱い
        status = OK if age_h < 96 else (WARN if age_h < 168 else CRIT)
        rep.add("DATA", status, f"xbrl_store 最終更新: {age_h/24:.1f}日前" + ("" if status == OK else " — 取込が止まっている疑い (yuho_audit で突合を)"))
    for f, label in ((FRONTEND_DATA / "index.json", "frontend index.json"),
                     (FRONTEND_DATA / "metrics_summary.json", "metrics_summary.json")):
        if not f.exists():
            rep.add("DATA", CRIT, f"{label} が存在しない: {f}")
            continue
        age_h = (now - datetime.fromtimestamp(f.stat().st_mtime)).total_seconds() / 3600
        status = OK if age_h < 96 else (WARN if age_h < 168 else CRIT)
        rep.add("DATA", status, f"{label} 最終更新: {age_h/24:.1f}日前" + ("" if status == OK else " — 再生成が止まっている疑い"))


# ===========================================================================
# 7. AUDITS (前回結果の読取り / --audit で実走)
# ===========================================================================
def check_audits(rep: Report, run_audits: bool) -> None:
    last = LOG_DIR / "yuho_audit_last.json"
    if last.exists():
        try:
            j = json.loads(last.read_text(encoding="utf-8"))
            ran = j.get("ran_at", "?")
            # 監査の鮮度: 前回PASSでも96時間 (週末考慮) 実行が無ければ「監査していない」と同じ
            stale_run = True
            try:
                stale_run = (datetime.now() - datetime.fromisoformat(ran)).total_seconds() > 96 * 3600
            except (ValueError, TypeError):
                pass
            if stale_run:
                rep.add("AUDITS", WARN, f"yuho_audit の前回実行が古い ({ran}) — 監査が回っていない。python yuho_audit.py --days 7 を実行")
            elif j.get("status") == "PASS":
                rep.add("AUDITS", OK, f"yuho_audit 前回PASS ({ran})")
            elif j.get("status") == "ENV_ERROR":
                rep.add("AUDITS", CRIT, f"yuho_audit 前回=環境エラー ({ran}): {'; '.join(j.get('problems', [])[:2])}")
            else:
                rep.add("AUDITS", WARN, f"yuho_audit 前回{j.get('status')} ({ran}): 欠落{j.get('hard_fail')}件 STALE{j.get('stale')}件 → python yuho_audit.py --days 7 で再確認")
        except (json.JSONDecodeError, OSError):
            rep.add("AUDITS", WARN, "yuho_audit_last.json が読めない")
    else:
        rep.add("AUDITS", WARN, "yuho_audit の実行記録なし → python yuho_audit.py --days 7 を一度実行")

    if run_audits:
        rc, out = run([PYTHON, str(PROJECT_ROOT / "yuho_audit.py"), "--days", "3", "--strict"], cwd=PROJECT_ROOT, timeout=900)
        tail = " / ".join(out.strip().splitlines()[-2:])
        rep.add("AUDITS", OK if rc == 0 else (CRIT if rc == 2 else WARN), f"yuho_audit --days 3 実走: rc={rc} {tail[:160]}")
        rc, out = run([PYTHON, str(PROJECT_ROOT / "tanshin_audit.py"), "--days", "2", "--strict"], cwd=PROJECT_ROOT, timeout=900)
        tail = " / ".join(out.strip().splitlines()[-2:])
        rep.add("AUDITS", OK if rc == 0 else WARN, f"tanshin_audit --days 2 実走: rc={rc} {tail[:160]}")


# ===========================================================================
# 8. GATES (--gates)
# ===========================================================================
def check_gates(rep: Report) -> None:
    scripts_dir = STOCKFLOW_ROOT / "frontend" / "scripts"
    for name in ("validate_payout.py", "scan_forbidden.py", "scan_report_quality.py"):
        s = scripts_dir / name
        if not s.exists():
            rep.add("GATES", WARN, f"{name} が見つからない")
            continue
        rc, out = run([PYTHON, str(s)], cwd=STOCKFLOW_ROOT / "frontend", timeout=600)
        tail = out.strip().splitlines()[-1] if out.strip() else ""
        rep.add("GATES", OK if rc == 0 else CRIT, f"{name}: {'PASS' if rc == 0 else 'FAIL'} {tail[:120]}")
    out_dir = STOCKFLOW_ROOT / "frontend" / "out"
    if out_dir.is_dir():
        rc, out = run([PYTHON, str(scripts_dir / "audit_meta.py")], cwd=STOCKFLOW_ROOT / "frontend", timeout=600)
        tail = out.strip().splitlines()[-1] if out.strip() else ""
        rep.add("GATES", OK if rc == 0 else CRIT, f"audit_meta.py: {'PASS' if rc == 0 else 'FAIL'} {tail[:120]}")
    else:
        rep.add("GATES", OK, "audit_meta.py: skip (out/ 未ビルド — ビルド後に実行)")


# ===========================================================================
# 9. HYGIENE
# ===========================================================================
def check_hygiene(rep: Report) -> None:
    cred = PROJECT_ROOT / "financial_analysis_system" / "credentials.json"
    if cred.exists():
        rc, _ = run(["git", "-C", str(PROJECT_ROOT), "check-ignore", "-q", str(cred)])
        if rc != 0:
            rep.add("HYGIENE", CRIT, "financial_analysis_system/credentials.json が gitignore されていない — 誤commitで秘密情報流出の危険。.gitignore に追加を")
        else:
            rep.add("HYGIENE", OK, "credentials.json は gitignore 済み")
    for nul in (PROJECT_ROOT / "nul", PROJECT_ROOT / "financial_analysis_system" / "nul"):
        if nul.exists():
            rep.add("HYGIENE", WARN, f"リダイレクト残骸 `{nul.relative_to(PROJECT_ROOT)}` が存在 (削除推奨: PowerShellで Remove-Item)")


# ===========================================================================
# Main
# ===========================================================================
def main() -> int:
    ap = argparse.ArgumentParser(description="kinmyakucode pipeline health check")
    ap.add_argument("--fast", action="store_true", help="git fetch を省略")
    ap.add_argument("--audit", action="store_true", help="tanshin/yuho audit を実走 (ネットワーク)")
    ap.add_argument("--gates", action="store_true", help="品質ゲートを実走")
    ap.add_argument("--full", action="store_true", help="--audit --gates 両方")
    ap.add_argument("--json", dest="json_out", action="store_true", help="JSON出力")
    args = ap.parse_args()
    if args.full:
        args.audit = args.gates = True

    rep = Report()
    check_mount(rep)
    check_task(rep)
    check_git(rep, do_fetch=not args.fast)
    check_actions(rep)
    check_logs(rep)
    check_data(rep)
    check_audits(rep, run_audits=args.audit)
    if args.gates:
        check_gates(rep)
    check_hygiene(rep)

    if args.json_out:
        print(json.dumps({"ran_at": datetime.now().isoformat(timespec="seconds"),
                          "worst": rep.worst, "sections": rep.sections}, ensure_ascii=False, indent=1))
        return rep.worst

    icons = {OK: "[OK]  ", WARN: "[WARN]", CRIT: "[CRIT]"}
    print(f"=== HEALTH CHECK {datetime.now().strftime('%Y-%m-%d %H:%M')} ===")
    cur = None
    for s in rep.sections:
        if s["section"] != cur:
            cur = s["section"]
            print(f"\n--- {cur} ---")
        print(f"  {icons[s['status']]} {s['message']}")
    n_warn = sum(1 for s in rep.sections if s["status"] == WARN)
    n_crit = sum(1 for s in rep.sections if s["status"] == CRIT)
    print(f"\n=== 結果: {'✅ ALL GREEN' if rep.worst == 0 else ('⚠️ WARN ' + str(n_warn) + '件' if rep.worst == 1 else '🚨 CRITICAL ' + str(n_crit) + '件 / WARN ' + str(n_warn) + '件')} ===")
    if rep.worst > 0:
        print("対処手順: OPS.md の該当セクションを参照 (Claude Code なら /daily-ops で診断+対処案まで出ます)")
    return rep.worst


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    sys.exit(main())
