"""打包产物冒烟验证：确认 dist\\PlanSticky.exe 真的能跑起来。

用法（PowerShell）：
    .venv\\Scripts\\python.exe tests\\exe_smoke.py

做的事：
1. 检查 dist\\PlanSticky.exe 是否存在、大小合理；
2. 在**临时数据目录**里启动它（`PLANSTICKY_AUTOQUIT_MS` 让它自己退出），
   避免碰到 `%APPDATA%\\PlanSticky` 里的真实数据；
3. 校验它确实完成了旧数据迁移、写下了 plans.db / ledger.db、
   并生成了 error.log 为空（无未捕获异常）；
4. 额外用 `--version` 之外的方式确认 exe 不是空壳：解压式单文件 exe
   首启动较慢，这里给足超时。

退出码 0 = 全部通过。
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXE = os.path.join(ROOT, "dist", "PlanSticky.exe")
LAUNCH_TIMEOUT = 180          # 单文件 exe 首次解压 + 启动 + 自动退出

FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    mark = "PASS" if cond else "FAIL"
    print(f"[{mark}] {name}" + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


def run_exe(data_dir: str, autoquit_ms: int = 4000,
            money_dir: str | None = None) -> tuple[int, str]:
    """在隔离的数据目录里启动 exe，返回 (退出码, 合并输出)。"""
    env = dict(os.environ)
    env["PLANSTICKY_DATA_DIR"] = data_dir
    env["PLANSTICKY_SINGLE"] = "0"
    env["PLANSTICKY_AUTOQUIT_MS"] = str(autoquit_ms)
    if money_dir:
        env["PLANSTICKY_MONEY_DIR"] = money_dir
    else:
        # 不指定就不迁移：指向一个不存在的目录，保证测试不依赖开发机
        env["PLANSTICKY_MONEY_DIR"] = os.path.join(data_dir, "__no_such_source__")
    env["QT_QPA_PLATFORM"] = "offscreen"      # 无显示环境下也能跑
    try:
        proc = subprocess.run([EXE], env=env, capture_output=True,
                              timeout=LAUNCH_TIMEOUT)
    except subprocess.TimeoutExpired:
        return -1, f"超时（>{LAUNCH_TIMEOUT}s 未退出）"
    output = (proc.stdout or b"").decode("utf-8", "replace") + \
             (proc.stderr or b"").decode("utf-8", "replace")
    return proc.returncode, output


def build_fake_source(root: str) -> None:
    """造一个最小旧库，验证 exe 内的迁移逻辑真的打包进去了。"""
    data = os.path.join(root, "data")
    os.makedirs(os.path.join(data, "journal-images"), exist_ok=True)
    os.makedirs(os.path.join(data, "journal-videos"), exist_ok=True)
    conn = sqlite3.connect(os.path.join(data, "ledger.db"))
    conn.executescript("""
        CREATE TABLE transactions (id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL, amount REAL NOT NULL, type TEXT NOT NULL DEFAULT 'expense',
            category TEXT NOT NULL, purpose TEXT NOT NULL, note TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE journals (date TEXT PRIMARY KEY, content TEXT NOT NULL,
            format TEXT NOT NULL DEFAULT 'text', updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE daily_checkins (date TEXT PRIMARY KEY, activity TEXT NOT NULL DEFAULT '',
            detail TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE checkin_tags (name TEXT PRIMARY KEY, color TEXT NOT NULL,
            sort_order INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
    """)
    conn.executemany(
        "INSERT INTO transactions(date,amount,type,category,purpose,note) VALUES(?,?,?,?,?,?)",
        [("2026-08-01", 28.5, "expense", "餐饮", "午餐", ""),
         ("2026-08-02", 8000.0, "income", "其他", "工资", "")])
    conn.execute("INSERT INTO settings(key,value) VALUES('budget','3500')")
    conn.execute("INSERT INTO journals(date,content,format) VALUES('2026-08-01','<div>exe 冒烟</div>','html')")
    conn.commit()
    conn.close()


def main() -> int:
    print("=" * 60)
    print("打包产物冒烟验证：dist\\PlanSticky.exe")
    print("=" * 60)

    check("exe 存在", os.path.isfile(EXE), EXE)
    if not os.path.isfile(EXE):
        print("\n没有产物，先运行 build_exe.bat 或 PyInstaller。")
        return 1

    size_mb = os.path.getsize(EXE) / 1024 / 1024
    check("大小合理（>20 MB，说明 Qt 打进去了）", size_mb > 20, f"{size_mb:.1f} MB")
    print(f"   产物大小：{size_mb:.1f} MB")

    tmp = tempfile.mkdtemp(prefix="plansticky_exe_")
    try:
        # ---- 第 1 次：不迁移，只确认能启动、能自建数据库 ----
        data1 = os.path.join(tmp, "run1")
        os.makedirs(data1, exist_ok=True)
        started = time.time()
        code, output = run_exe(data1)
        elapsed = time.time() - started
        check("exe 正常退出（autoquit）", code == 0, f"exit={code}\n{output[-1500:]}")
        print(f"   启动到退出耗时：{elapsed:.1f}s")

        plans_db = os.path.join(data1, "plans.db")
        ledger_db = os.path.join(data1, "ledger.db")
        check("生成了 plans.db", os.path.isfile(plans_db))
        check("生成了 ledger.db", os.path.isfile(ledger_db))
        check("生成了日记媒体目录",
              os.path.isdir(os.path.join(data1, "journal-images"))
              and os.path.isdir(os.path.join(data1, "journal-videos")))

        log = os.path.join(data1, "error.log")
        if os.path.isfile(log):
            content = open(log, encoding="utf-8", errors="replace").read().strip()
            check("没有未捕获异常（error.log 为空）", not content, content[-800:])
        else:
            check("没有未捕获异常（无 error.log）", True)

        # 数据库结构在 exe 里也完整
        if os.path.isfile(ledger_db):
            conn = sqlite3.connect(ledger_db)
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            conn.close()
            need = {"transactions", "journals", "daily_checkins", "checkin_tags", "settings"}
            check("ledger.db 表结构完整", need <= tables, str(need - tables))

        # ---- 第 2 次：带旧数据，验证迁移逻辑被打包进去 ----
        source = os.path.join(tmp, "money")
        os.makedirs(source, exist_ok=True)
        build_fake_source(source)
        data2 = os.path.join(tmp, "run2")
        os.makedirs(data2, exist_ok=True)
        code2, output2 = run_exe(data2, money_dir=source)
        check("带旧数据启动正常退出", code2 == 0, f"exit={code2}\n{output2[-1500:]}")

        ledger2 = os.path.join(data2, "ledger.db")
        check("迁移后生成 ledger.db", os.path.isfile(ledger2))
        if os.path.isfile(ledger2):
            conn = sqlite3.connect(ledger2)
            tx = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
            income = conn.execute(
                "SELECT COUNT(*) FROM transactions WHERE type='income'").fetchone()[0]
            journals = conn.execute("SELECT COUNT(*) FROM journals").fetchone()[0]
            budget = conn.execute(
                "SELECT value FROM settings WHERE key='budget'").fetchone()
            imported = conn.execute(
                "SELECT value FROM settings WHERE key='money_imported'").fetchone()
            conn.close()
            check("账目迁移过来", tx == 2, str(tx))
            check("收入类型保持正确", income == 1, str(income))
            check("日记迁移过来", journals == 1, str(journals))
            check("预算迁移过来", budget is not None and budget[0] == "3500",
                  str(budget))
            check("写入了导入标记", imported is not None and imported[0] == "1",
                  str(imported))

        # ---- 第 3 次：重复启动不得重复导入 ----
        code3, output3 = run_exe(data2, money_dir=source)
        check("重复启动正常退出", code3 == 0, f"exit={code3}\n{output3[-800:]}")
        if os.path.isfile(ledger2):
            conn = sqlite3.connect(ledger2)
            tx3 = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
            conn.close()
            check("重复启动没有重复导入", tx3 == 2, str(tx3))

        # ---- 源目录必须只读 ----
        src_db = os.path.join(source, "data", "ledger.db")
        conn = sqlite3.connect(f"file:{src_db}?mode=ro", uri=True)
        src_tx = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        conn.close()
        check("源库未被改动", src_tx == 2, str(src_tx))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "-" * 60)
    if FAILURES:
        print(f"EXE SMOKE FAILED: {len(FAILURES)} 项失败")
        for name in FAILURES:
            print("  x", name)
        return 1
    print("EXE SMOKE PASSED: 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
