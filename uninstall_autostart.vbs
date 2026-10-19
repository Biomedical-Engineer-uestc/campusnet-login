Option Explicit

Dim fso, shell, startupDir, linkPath
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

startupDir = shell.SpecialFolders("Startup")
linkPath = startupDir & "\CampusNet.lnk"

If fso.FileExists(linkPath) Then
    fso.DeleteFile linkPath, True
End If
