# Tools and Data for Stat Var Embeddings Index

This directory contains the data CSV (containing StatVar DCID and
descriptions) and script used to construct the stat var embeddings index that
is loaded into the NL Server.

There are multiple embeddings index types. Each index holds the stat var
descriptions for a particular domain or use case. The input stat var
description csvs for one index type are saved in one folder under `input/`.

## Embeddings Index Config

Embeddings index is configured in
[`catalog.yaml`](../../../deploy/nl/catalog.yaml), under `indexes` field. The
keys are index names (specified as `idx=` param value). Each value contains the
following fields:

- `store_type`: what type of embeddings store? (MEMORY, LANCEDB, VERTEXAI)
- `model`: the name of the associated model from `models` section
- `embeddings_path`: For MEMORY/LANCEDB, the path to the index files. Can be a
  local absolute path or GCS (gs://) path.
- `source_path`: the input csv folder path.

### Create New Index Config

To create a new index type, add an entry under `indexes`, fill in `store_type`,
`model`, `source_path`. This is sufficient to build the index as indicated in
the steps below.

## Input CSV Format

Curated stat var and topics description should be saved in csv files. Each csv
file should have two columns:

- `dcid`: the StatVar DCID.
- `sentence`: the description(s) of the StatVar. Multiple descriptions are
  acceptable. If multiple description strings are provided, they must be
  semi-colon delimited.

## Update Stat Var Descriptions

To easily edit the curated csv in Google sheets, you can go use the sheets
command-line tools [here](../../sheets/).

- E.g., To copy the curated input for the base embeddings to a google sheet, go
  to the sheets command line tools folder and run:

<<<<<<< HEAD
   ```bash
   ./run.sh -f small
   ```
=======
  ```bash
  ./run.sh -m csv2sheet -l ../nl/embeddings/input/base/sheets_svs.csv [-s <sheets_url>] [-w <worksheet_name>]
  ```
>>>>>>> staging

- E.g., To copy the contents of the google sheet back as the curated input for
  the base embeddings, go to the sheets command line tools folder and run:

  ```bash
  ./run.sh -m sheet2csv -l ../nl/embeddings/input/base/sheets_svs.csv -s <sheets_url> -w <worksheet_name>
  ```

## Build Embeddings Index

<<<<<<< HEAD
   ```bash
   ./run.sh -c sdg data/curated_input/sdg data/alternatives/sdg/*.csv
   ```
=======
Identify the index name from [catalog.yaml](../../../deploy/nl/catalog.yaml),
within `indexes`. If the index is newly created, add a new entry as described
above.
>>>>>>> staging

Run the command below which will generate a new embeddings csv in
`gs://datcom-nl-models`. Note down the embeddings file version printed at the
end of the run.

<<<<<<< HEAD
   ```bash
   ./run.sh -c undata data/curated_input/undata data/alternatives/undata/*.csv
   ```

   To generate the `undata_ilo_ft` embeddings:

   ```bash
   ./run.sh -c undata_ilo data/curated_input/undata_ilo
   ```
=======
```bash
./run.sh -e <EMBEDDINGS_NAME>
```
>>>>>>> staging

Available options for <EMBEDDINGS_NAME> are:

<<<<<<< HEAD
   ```bash
   ./run.sh -c bio data/curated_input/bio,data/curated_input/main data/alternatives/main/*.csv
   ```
=======
- sdg_ft
- undata_ft
- undata_ilo_ft
- bio_ft
- base_uae_mem
>>>>>>> staging

## Validate Embeddings Index

1. Validate the CSV diffs, update
   [`catalog.yaml`](../../../deploy/nl/catalog.yaml) with the generated
   embeddings path and test out locally.

1. Generate an SV embeddings differ report by following the process under the
   [`sv_index_differ`](../svindex_differ/README.md) folder. Look
   at the diffs and evaluate whether they make sense.

1. Update goldens by running `./run_test.sh -g` from the repo root.

<<<<<<< HEAD
   All the endpoints can be found in this [page](https://pantheon.corp.google.com/vertex-ai/online-prediction/endpoints?mods=-monitoring_api_staging&project=datcom-website-dev).

   TODO: Add improved alternative descriptions to undata topics

   You can also create custom embeddings (using the finetuned model in PROD):

   ```bash
   ./run.sh -c <embeddings_size> <curated_input_csv_dirs> <alternatives_filepattern>
   ```

   Notes:

   - curated_input_dirs is a list of directories separated by `,` which contains the CSVs with the curated inputs to use. The format of the CSVs should follow the description of [point 1](#curated-input).
   - alternatives_filepattern is the filepattern of the CSV files with the alternatives to use. The format of the CSVs should follow the description of [point 3](#alternatives).
   - Example: `./run.sh -c test data/curated_input/test data/alternatives/test/*.csv`

5. Validate the CSV diffs, update [`embeddings.yaml`](../../../deploy/nl/embeddings.yaml) with the generated embeddings version and test out locally.

6. Generate an SV embeddings differ report by following the process under the [`sv_index_differ`](../svindex_differ/README.md) folder (one level up). Look at the diffs and evaluate whether they make sense.

7. Update goldens by running `./run_test.sh -g` from the repo root.

8. If everything looks good, send out a PR with the `embeddings.yaml`, the `differ_report.html` file (as a linked attachement), CSV changes, and updated goldens.

## Production Config Files

### [`embeddings.yaml`](../../../deploy/nl/embeddings.yaml)

Lists the embeddings CSV files (generated using the steps above).

The keys are index names (specified as `idx=` param value), and the values are file names (with the assumption that the files are stored in gs://datcom-nl-models/).

These files, generated from a fine-tuned model (as of Q2 2023), have the following structure: `<version>.<fine-tuned-model-version>.<base-model-name>.csv` (e.g., `datcom-nl-models/embeddings_sdg_2023_09_12_16_38_04.ft_final_v20230717230459.all-MiniLM-L6-v2.csv`).

### [`models.yaml`](../../../deploy/nl/models.yaml)

A mostly legacy file that lists the fine-tuned model name.
=======
1. If everything looks good, send out a PR with the `catalog.yaml`, the
   `differ_report.html` file (as a linked attachement), CSV changes, and updated
   goldens.
>>>>>>> staging
