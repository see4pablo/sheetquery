# SheetQuery — TODO

## Segmentation off modes

- [ ] **Support turning off age and/or gender segmentation end-to-end**  
  Today the happy path assumes both `AGE_SEGMENTATION` and `GENDER_SEGMENTATION` are on (wide age×sex tables, browser CSV headers, `resolve_age_sex_columns`, `table_to_batch_values`).  
  Need a working path when either or both flags are `false` in `config/segmentation.conf` (and in the web UI toggles):

  - count output shape (single total, age-only, or sex-only)
  - batch / browser CSV headers (`browser_api.batch_named_header_rows`, `table_to_batch_values`)
  - loading a sheet that may omit age or sex columns when that axis is disabled
  - interactive + batch desktop flows, not only the Pyodide UI
  - tests for each combination: age on/off × gender on/off
