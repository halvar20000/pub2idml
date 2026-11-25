-- pub2idml.app - drop Microsoft Publisher files (.pub) or folders on the icon
-- to convert them to IDML. Double-click the app to choose files instead.
-- The command-line tools (pub2idml, pub2json, libs/) live in Contents/Resources/bin.
--
-- SPDX-License-Identifier: MIT

on run
	if my isGerman() then
		set promptText to "Publisher-Dateien (.pub) zum Umwandeln in IDML auswählen:"
	else
		set promptText to "Choose Microsoft Publisher files (.pub) to convert to IDML:"
	end if
	try
		set picked to choose file with prompt promptText of type {"pub"} with multiple selections allowed
	on error number -128 -- cancelled
		return
	end try
	my handleItems(picked)
end run

on open droppedItems
	my handleItems(droppedItems)
end open

on handleItems(theItems)
	set pathList to {}
	repeat with anItem in theItems
		set end of pathList to POSIX path of (contents of anItem)
	end repeat
	set binDir to (POSIX path of (path to me)) & "Contents/Resources/bin/"
	set r to my convertPaths(pathList, binDir, true)
	my showResult(r, my isGerman())
end handleItems

-- Converts the given POSIX paths (files or folders). Returns a summary record.
-- Folders contribute the .pub files directly inside them.
on convertPaths(pathList, binDir, showProgress)
	set exe to binDir & "pub2idml"
	set pubFiles to {}
	set skippedCount to 0
	repeat with p in pathList
		set p to contents of p
		if p ends with "/" then
			set found to paragraphs of (do shell script "find " & quoted form of p & " -maxdepth 1 -type f -iname '*.pub' | sort")
			repeat with f in found
				if (contents of f) is not "" then set end of pubFiles to (contents of f)
			end repeat
		else if p ends with ".pub" or p ends with ".jsonl" then -- comparisons ignore case
			set end of pubFiles to p
		else
			set skippedCount to skippedCount + 1
		end if
	end repeat

	set okCount to 0
	set warnCount to 0
	set failures to {}
	set lastOut to ""
	set n to count of pubFiles
	if showProgress and n > 0 then
		set progress total steps to n
		set progress completed steps to 0
		set progress description to "pub2idml"
	end if
	repeat with i from 1 to n
		set f to item i of pubFiles
		if showProgress then set progress additional description to my baseName(f)
		try
			set outText to do shell script quoted form of exe & " " & quoted form of f & " 2>&1"
			set okCount to okCount + 1
			set lastOut to my idmlPath(f)
			repeat with l in paragraphs of outText
				if (contents of l) contains "warning:" then set warnCount to warnCount + 1
			end repeat
		on error errMsg
			set end of failures to (my baseName(f)) & ": " & my reason(errMsg)
		end try
		if showProgress then set progress completed steps to i
	end repeat
	return {total:n, converted:okCount, warnings:warnCount, failures:failures, skipped:skippedCount, lastOutput:lastOut}
end convertPaths

on showResult(r, de)
	set n to total of r
	if n is 0 then
		if de then
			set msg to "Keine .pub-Dateien gefunden." & return & return & "Ziehen Sie Microsoft-Publisher-Dateien (.pub) oder einen Ordner mit solchen Dateien auf das pub2idml-Symbol."
		else
			set msg to "No .pub files found." & return & return & "Drop Microsoft Publisher files (.pub), or a folder containing them, on the pub2idml icon."
		end if
		display dialog msg buttons {"OK"} default button 1 with icon caution with title "pub2idml"
		return
	end if

	set ok to converted of r
	if de then
		set msg to (ok as text) & " von " & (n as text) & " Datei(en) umgewandelt."
		if ok > 0 then set msg to msg & return & "Die IDML-Dateien liegen neben den Originalen."
		if (warnings of r) > 0 then set msg to msg & return & return & "Hinweise: " & ((warnings of r) as text) & " (z. B. nicht unterstützte Elemente wie Tabellen)."
		if (count of (failures of r)) > 0 then set msg to msg & return & return & "Fehler:"
		set showButton to "Im Finder zeigen"
	else
		set msg to (ok as text) & " of " & (n as text) & " file(s) converted."
		if ok > 0 then set msg to msg & return & "The IDML files are next to the originals."
		if (warnings of r) > 0 then set msg to msg & return & return & "Notes: " & ((warnings of r) as text) & " (e.g. unsupported elements such as tables)."
		if (count of (failures of r)) > 0 then set msg to msg & return & return & "Errors:"
		set showButton to "Show in Finder"
	end if
	repeat with f in failures of r
		set msg to msg & return & "• " & (contents of f)
	end repeat

	if (count of (failures of r)) > 0 then
		set theIcon to caution
	else
		set theIcon to note
	end if
	if ok > 0 then
		set answer to display dialog msg buttons {showButton, "OK"} default button "OK" with icon theIcon with title "pub2idml"
		if button returned of answer is showButton then
			do shell script "open -R " & quoted form of (lastOutput of r)
		end if
	else
		display dialog msg buttons {"OK"} default button "OK" with icon theIcon with title "pub2idml"
	end if
end showResult

-- helpers ------------------------------------------------------------------

on isGerman()
	try
		return (user locale of (system info)) starts with "de"
	on error
		return false
	end try
end isGerman

on baseName(p)
	set saved to AppleScript's text item delimiters
	set AppleScript's text item delimiters to "/"
	set b to last text item of p
	set AppleScript's text item delimiters to saved
	return b
end baseName

on idmlPath(p)
	set saved to AppleScript's text item delimiters
	set AppleScript's text item delimiters to "."
	set parts to text items of p
	if (count of parts) > 1 then set parts to items 1 thru -2 of parts
	set stem to parts as text
	set AppleScript's text item delimiters to saved
	return stem & ".idml"
end idmlPath

-- The useful part of pub2idml's error output: its last line, without "FAIL <path>: ".
on reason(errMsg)
	set lastLine to ""
	repeat with l in paragraphs of errMsg
		if (contents of l) is not "" then set lastLine to (contents of l)
	end repeat
	set marker to ".pub: "
	if lastLine contains marker then
		set saved to AppleScript's text item delimiters
		set AppleScript's text item delimiters to marker
		set lastLine to last text item of lastLine
		set AppleScript's text item delimiters to saved
	end if
	return lastLine
end reason
