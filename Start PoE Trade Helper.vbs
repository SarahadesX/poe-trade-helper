' Launches PoE Trade Helper with NO visible console window.
' Runs plain python.exe directly (not pythonw, not via cmd) so stdout/logging
' still work while the window stays hidden. Closing the browser tab stops the
' server by itself, so no console is needed.
Option Explicit
Dim sh, fso, dir
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = dir

On Error Resume Next
' Auto-update if this folder was cloned with git (silent).
If fso.FolderExists(dir & "\.git") Then
    sh.Run "git pull --ff-only", 0, True
End If
' First run: create a local config from the template.
If (Not fso.FileExists(dir & "\config.json")) And fso.FileExists(dir & "\config.example.json") Then
    fso.CopyFile dir & "\config.example.json", dir & "\config.json"
End If
On Error GoTo 0

' Start the server hidden (window style 0 = hidden, don't wait).
sh.Run "python app.py", 0, False
