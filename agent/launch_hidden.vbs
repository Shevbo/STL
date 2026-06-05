' Hidden launcher for the Shectory optimizer agent.
' Runs agent\run_agent.cmd with NO window (style 0) and waits (True) so the launching
' Scheduled Task stays "running" and restart-on-failure works. Self-locating: derives
' the repo from this script's own path, so it works wherever the repo lives (the path
' may contain spaces and "&"). The wrapper handles self-update restarts internally.
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh  = CreateObject("WScript.Shell")
agentDir = fso.GetParentFolderName(WScript.ScriptFullName)
repo     = fso.GetParentFolderName(agentDir)
sh.CurrentDirectory = repo
sh.Run """" & agentDir & "\run_agent.cmd""", 0, True
