Set shell = CreateObject("WScript.Shell")
base = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
command = "cmd.exe /c " & Chr(34) & Chr(34) & base & "\start_standalone.cmd" & Chr(34) & Chr(34)
shell.CurrentDirectory = base
shell.Run command, 0, False
