Option Explicit

Dim fso, shell, baseDir, command
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

baseDir = fso.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = baseDir

command = "pythonw.exe """ & baseDir & "\campus_net.py"" --watch --quiet"

On Error Resume Next
shell.Run command, 0, False
If Err.Number <> 0 Then
    Err.Clear
    shell.Run "python.exe """ & baseDir & "\campus_net.py"" --watch --quiet", 0, False
End If
