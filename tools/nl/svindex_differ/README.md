# StatVar Embeddings Differ

This is a command-line tool to compare a pair of variable embeddings indexes.
<<<<<<< HEAD
An index is made up of a model + store identified in `embeddings.yaml`.  You
can compare two indexes where just the model, or the store, or both can be
different.

The tool runs a bunch of golden queries (in [queryset.csv](queryset.csv))
against a `test_index` and a `base_index`, and reports diffs in an HTML
report that is saved in [gs://datcom-embedding-diffs](https://pantheon.corp.google.com/storage/browser/datcom-embedding-diffs).

The base index details are from the checked in `embeddings.yaml` and the test
index details are from `embeddings.yaml` in the local client.

If you are using the tool in the context of updating the embeddings store, as
the first step, produce a new store following instructions
[here](../embeddings). And then, update the local `embeddings.yaml` file with
=======
An embedding index is made up of a model + store identified in `catalog.yaml`.
You can compare two indexes where just the model, or the store, or both can be
different.

The tool runs a bunch of golden queries (in [queryset_vars.csv](queryset_vars.csv))
against a `test_index` and a `base_index`, and reports diffs in an HTML
report that is saved in [gs://datcom-embedding-diffs](https://pantheon.corp.google.com/storage/browser/datcom-embedding-diffs).

The base index details are from the checked in `catalog.yaml` and the test
index details are from `catalog.yaml` in the local client.

If you are using the tool in the context of updating the embeddings store, as
the first step, produce a new store following instructions
[here](../embeddings). And then, update the local `catalog.yaml` file with
>>>>>>> staging
the new SV index path.

## Run the tool

<<<<<<< HEAD
Run the tool as below.  You need the `autopush` Mixer API key.  If
=======
Run the tool as below. You need the `autopush` Mixer API key. If
>>>>>>> staging
`TEST_INDEX` is not provided, then it is the same as `BASE_INDEX`.

```bash
export AUTOPUSH_KEY=<XYZ>
./run.sh <BASE_INDEX> [<TEST_INDEX>]
```

<<<<<<< HEAD
Example #1: `./run.sh medium_ft` compares the store for the index between
local and repo master.

Example #2: `./run.sh medium_ft medium_v2_vertexai_ft` compares two different
=======
Example #1: `./run.sh base_uae_mem` compares the store for the index between
local and repo master.

Example #2: `./run.sh medium_ft base_uae_mem` compares two different
>>>>>>> staging
indexes which differ in both model and store.

NOTE: Please ensure any diffs are expected/understood, and attach the report
to the PR updating the SV index.
<<<<<<< HEAD
=======

### Run the tool with custom query transforms

- To compare with and without stop-words for a given index:

```bash
export AUTOPUSH_KEY=<XYZ>
./run.sh <SAME_INDEX> <SAME_INDEX> \
--base_query_transform=STRIP_STOP_WORDS \
--queryset=tools/nl/svindex_differ/queryset_vars_withstopwords.csv
```
>>>>>>> staging
