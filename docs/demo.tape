# vhs script — the 10-minute happy path, recorded in ~40s.
# Render: vhs docs/demo.tape   (writes docs/demo.gif, per the Output line)
# CI refresh: .github/workflows/demo.yml (manual dispatch only).
#
# Every command below is a documented quickstart command run against the
# bundled synthetic demo dataset; no model calls and no network are involved.

Output docs/demo.gif

Set Shell "bash"
Set FontSize 16
Set Width 1240
Set Height 620
Set Padding 20
Set Theme "Dracula"
Set TypingSpeed 22ms

Hide
Type "uv sync -q && source .venv/bin/activate && clear"
Enter
Sleep 8s
Show

Type "# 1. what did I actually run? describe the design"
Enter
Sleep 400ms
Type "deltagauge describe"
Enter
Sleep 2s

Type "# 2. the error budget behind every verdict"
Enter
Sleep 400ms
Type "deltagauge gauge"
Enter
Sleep 2.5s

Type "# 3. is the one-point delta real? (delta -1.0 vs decision limit +/-1.4)"
Enter
Sleep 400ms
Type "deltagauge compare spark-open qwen3.8-flash-next"
Enter
Sleep 2.5s

Type "# 4. normalize your own export into the canonical run set"
Enter
Sleep 400ms
Type "deltagauge ingest examples/spark-open-vs-qwen3.8-flash-next.csv --from csv --out runs.csv"
Enter
Sleep 2.5s

Type "# 5. the shareable artifact for a procurement writeup"
Enter
Sleep 400ms
Type "deltagauge report --out report.md && head -20 report.md"
Enter
Sleep 3s
