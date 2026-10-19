Option Explicit

Dim fso, shell, baseDir, startupDir, link
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

baseDir = fso.GetParentFolderName(WScript.ScriptFullName)
startupDir = shell.SpecialFolders("Startup")

Set link = shell.CreateShortcut(startupDir & "\CampusNet.lnk")
link.TargetPath = baseDir & "\run_watch.vbs"
link.WorkingDirectory = baseDir
link.Description = "CampusNet campus network auto login"
link.WindowStyle = 7
link.Save
