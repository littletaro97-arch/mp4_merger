Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
folder = fso.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = folder
If fso.FileExists(folder & "\dist\MediaToolbox\MediaToolbox.exe") Then
 shell.Run Chr(34) & folder & "\dist\MediaToolbox\MediaToolbox.exe" & Chr(34), 1, False
Else
 shell.Run "pythonw.exe " & Chr(34) & folder & "\main.py" & Chr(34), 0, False
End If
