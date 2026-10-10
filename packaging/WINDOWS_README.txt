SheetQuery — how to use it (Windows)
====================================

Use this package WITHOUT installing Python or editing code.

1) data\ folder
   - Copy your spreadsheet here (.csv or .xlsx), e.g. yeco.csv
   - If there is only one file, the program uses it automatically
   - If there are several, it uses the most recently modified one
   - You can also set a fixed name in config\run.conf → DATA_FILE=data\your_file.csv

2) queries\ folder
   - dani_y.json contains the prepared queries
   - For another set, change config\run.conf → QUERIES_FILE=queries\other.json

3) Run
   - Double-click SheetQuery.exe
   - Wait until it finishes (a console window and a final dialog)

4) Results
   - Look in the output\ folder
   - The batch_....csv file is the grid (one row per query)
   - Open it with Excel

If something fails
   - Read the message in the console or the dialog
   - Check that the spreadsheet has age and sex columns
   - Check that queries\dani_y.json exists

Notes
   - Do not upload spreadsheets with patient data to the internet
   - SILENT_MODE in config\app.conf is true: only the batch file is written
     (not one CSV per query)

Credits
   - Software: Pablo (MIT License; see LICENSE in the project)
   - dani_y queries: set prepared with / for Dani
   - This program was developed with AI assistance (AI-assisted coding tools).
     Responsibility for use remains with the person who runs it and the
     project author.
