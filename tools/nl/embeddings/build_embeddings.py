# Copyright 2024 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Build the embeddings index from variable and topic descriptions."""

import logging
import sys

from absl import app
from absl import flags
<<<<<<< HEAD
from google.cloud import aiplatform
from google.cloud import storage
import lancedb
import pandas as pd
from sentence_transformers import SentenceTransformer
import utils

VERTEX_AI_PROJECT = 'datcom-nl'
VERTEX_AI_PROJECT_LOCATION = 'us-central1'

FLAGS = flags.FLAGS

# TODO: use only one flag from the two below and "gcs://" prefix to differentiate
# between local and GCS path.
flags.DEFINE_string('finetuned_model_gcs', '',
                    'Existing finetuned model folder name on GCS')
flags.DEFINE_string('existing_model_path', '',
                    'Path to an existing model (local)')
flags.DEFINE_string(
    'vertex_ai_prediction_endpoint_id', '',
    'The ID of vertex AI prediction endpoint.' +
    ' Not set for local Sentence Transformers based index, must be set for API-based index'
)
flags.DEFINE_string(
    'lancedb_output_path', '', 'The output path to produce LanceDB index. ' +
    'Currently always uses SentenceTransformer model.')
flags.DEFINE_string('model_name_v2', 'all-MiniLM-L6-v2', 'Model name')
flags.DEFINE_string('bucket_name_v2', 'datcom-nl-models', 'Storage bucket')
flags.DEFINE_string('embeddings_size', '', 'Embeddings size')

flags.DEFINE_list('curated_input_dirs', ['data/curated_input/main'],
                  'Curated input csv (relative) directory list')
=======

from nl_server import config_reader
from tools.nl.embeddings import utils

FLAGS = flags.FLAGS

flags.DEFINE_string('embeddings_name', '',
                    'Embeddings name as specified in catalog.yaml')

flags.DEFINE_string('output_dir', '',
                    'The output directory to save the embeddings files/db')
>>>>>>> staging

flags.DEFINE_string(
    'additional_catalog_path', '',
    'Path to an additional catalog yaml file. Can be a local or a GCS path')

_LANCEDB_TABLE = 'datacommons'


<<<<<<< HEAD
def _make_gcs_embeddings_filename(embeddings_size: str,
                                  model_version: str) -> str:
  now = datetime.datetime.now()
  formatted_date_string = now.strftime("%Y_%m_%d_%H_%M_%S")
  return f"embeddings_{embeddings_size}_{formatted_date_string}.{model_version}.csv"


def _make_embeddings_index_filename(embeddings_size: str,
                                    model_endpoint_id: str) -> str:
  now = datetime.datetime.now()
  formatted_date_string = now.strftime("%Y_%m_%d_%H_%M_%S")
  return f"embeddings_{embeddings_size}_{formatted_date_string}.{model_endpoint_id}.json"


def _write_intermediate_output(name2sv_dict: Dict[str, str],
                               dup_sv_rows: List[List[str]],
                               local_merged_filepath: str,
                               dup_names_filepath: str) -> None:
  sv2names = {}
  for name, sv in name2sv_dict.items():
    if sv not in sv2names:
      sv2names[sv] = []
    sv2names[sv].append(name)

  sv_list = sorted(list(sv2names.keys()))
  name_list = [';'.join(sorted(sv2names[v])) for v in sv_list]

  # Write to local_merged_filepath.
  print(
      f"Writing the concatenated dataframe after merging alternates to local file: {local_merged_filepath}"
  )
  df_svs = pd.DataFrame({'dcid': sv_list, 'sentence': name_list})
  df_svs.to_csv(local_merged_filepath, index=False)

  if dup_names_filepath:
    print(f"Writing duplicate names file: {dup_names_filepath}")
    with open(dup_names_filepath, 'w') as f:
      csv.writer(f).writerows(dup_sv_rows)


def get_embeddings(ctx, df_svs: pd.DataFrame, local_merged_filepath: str,
                   dup_names_filepath: str) -> pd.DataFrame:
  print(f"Concatenate all alternative sentences for descriptions.")
  alternate_descriptions = []
  for _, row in df_svs.iterrows():
    alternatives = []
    if row[utils.OVERRIDE_COL]:
      # Override takes precendence over everything else.
      alternatives += utils.split_alt_string(row[utils.OVERRIDE_COL])
    else:
      for col_name in [
          utils.NAME_COL,
          utils.DESCRIPTION_COL,
          utils.CURATED_ALTERNATIVES_COL,
          utils.ALTERNATIVES_COL,
      ]:
        if col_name not in row:
          continue
        # In order of preference, traverse the various alternative descriptions.
        alternatives += utils.split_alt_string(row[col_name])

    alt_str = utils.concat_alternatives(alternatives, MAX_ALTERNATIVES_LIMIT)
    alternate_descriptions.append(alt_str)

  assert len(df_svs) == len(alternate_descriptions)
  df_svs[utils.COL_ALTERNATIVES] = alternate_descriptions
  # Trim df
  df_svs = df_svs[[utils.DCID_COL, utils.COL_ALTERNATIVES]]

  # Dedupe texts
  (text2sv_dict, dup_sv_rows) = utils.dedup_texts(df_svs)

  # Write dcid -> texts and dups to intermediate files.
  _write_intermediate_output(text2sv_dict, dup_sv_rows, local_merged_filepath,
                             dup_names_filepath)

  print("Building embeddings")
  return utils.build_embeddings(ctx, text2sv_dict)


def build(ctx, curated_input_dirs: List[str], local_merged_filepath: str,
          dup_names_filepath: str, autogen_input_filepattern: str,
          alternative_filepattern: str) -> pd.DataFrame:
  curated_input_df_list = list()
  # Read curated sv info.
  for curated_input_dir in curated_input_dirs:
    for file_path in glob.glob(curated_input_dir + "/*.csv"):
      try:
        print(f"Reading the curated input file: {file_path}")
        file_df = pd.read_csv(file_path, na_filter=False)
        curated_input_df_list.append(file_df)
      except:
        print("Error reading curated input file: {file_path}")

  if curated_input_df_list:
    # Use inner join to only add rows that have the same headings (which all
    # curated inputs should have the same headings)
    df_svs = pd.concat(curated_input_df_list, join="inner")
  else:
    df_svs = pd.DataFrame()

  # Append autogen CSVs if any.
  autogen_dfs = []
  for autogen_csv in sorted(glob.glob(autogen_input_filepattern)):
    print(f'Processing autogen input file: {autogen_csv}')
    autogen_dfs.append(pd.read_csv(autogen_csv).fillna(""))
  if autogen_dfs:
    df_svs = pd.concat([df_svs] + autogen_dfs)
    df_svs = df_svs.drop_duplicates(subset=utils.DCID_COL)

  # Get alternatives and add to the dataframe.
  if alternative_filepattern:
    for alt_fp in sorted(glob.glob(alternative_filepattern)):
      df_alts = utils.get_local_alternatives(
          alt_fp, [utils.DCID_COL, utils.ALTERNATIVES_COL])
      df_svs = utils.merge_dataframes(df_svs, df_alts)

  return get_embeddings(ctx, df_svs, local_merged_filepath, dup_names_filepath)


def write_row_to_jsonl(f, row):
  dcid = row[utils.DCID_COL]  # Get the DCID value
  text = row[utils.COL_ALTERNATIVES]  # Get the text
  embedding = row.drop([utils.DCID_COL, utils.COL_ALTERNATIVES
                       ]).tolist()  # Get the embeddings as a list
  f.write(
      json.dumps({
          'id': text,
          'embedding': embedding,
          'restricts': [{
              'namespace': 'dcid',
              'allow': [dcid]
          }]
      }))
  f.write('\n')
=======
def _init_logger():
  # Log to stdout for easy redirect of the output text.
  # This enables the logs to be captured by the admin tool.
  logger = logging.getLogger()
  logger.setLevel(logging.INFO)
  handler = logging.StreamHandler(sys.stdout)
  handler.setLevel(logging.INFO)
  logger.addHandler(handler)
>>>>>>> staging


def get_lancedb_records(df) -> List[Dict]:
  dcids = df[utils.DCID_COL].values.tolist()
  sentences = df[utils.COL_ALTERNATIVES].values.tolist()
  df = df.drop(utils.DCID_COL, axis=1)
  df = df.drop(utils.COL_ALTERNATIVES, axis=1)
  vectors = df.to_numpy().tolist()
  records = []
  for d, s, v in zip(dcids, sentences, vectors):
    records.append({'dcid': d, 'sentence': s, 'vector': v})
  return records


def main(_):
<<<<<<< HEAD
  assert FLAGS.vertex_ai_prediction_endpoint_id or (FLAGS.model_name_v2 and
                                                    FLAGS.bucket_name_v2 and
                                                    FLAGS.curated_input_dirs)
=======
  _init_logger()
>>>>>>> staging

  # Get embeddings_name
  embeddings_name = FLAGS.embeddings_name
  output_dir = FLAGS.output_dir

  assert embeddings_name, output_dir

  catalog = config_reader.read_catalog(
      additional_catalog_path=FLAGS.additional_catalog_path)
  index_config = catalog.indexes[embeddings_name]
  # Use default env config: autopush for base DCs and custom env for custom DCs.
  env = config_reader.read_env()
  model = utils.get_model(catalog, env, index_config.model)

  # Construct a file manager
  input_dir = index_config.source_path
  fm = utils.FileManager(input_dir, output_dir)

  # Build and save preindex
  preindexes = utils.build_and_save_preindexes(fm)

  # Load existing embeddings from previous run.
  existing_embeddings = utils.load_existing_embeddings(
      index_config.embeddings_path)

  # Compute embeddings
  final_embeddings = utils.compute_embeddings(model, preindexes,
                                              existing_embeddings)

  # Save embeddings
  if index_config.store_type == 'MEMORY':
    utils.save_embeddings_memory(fm.local_output_dir(), final_embeddings)
  elif index_config.store_type == 'LANCEDB':
    utils.save_embeddings_lancedb(fm.local_output_dir(), final_embeddings)
  else:
    raise ValueError(f'Unknown store type: {index_config.store_type}')

  # Save index config
  utils.save_index_config(fm, index_config)

<<<<<<< HEAD
  if FLAGS.vertex_ai_prediction_endpoint_id:
    model_version = FLAGS.vertex_ai_prediction_endpoint_id
    embeddings_index_json_filename = _make_embeddings_index_filename(
        FLAGS.embeddings_size, FLAGS.vertex_ai_prediction_endpoint_id)
    embeddings_index_tmp_out_path = os.path.join(
        ctx.tmp, embeddings_index_json_filename)

  gcs_embeddings_filename = _make_gcs_embeddings_filename(
      FLAGS.embeddings_size, model_version)
  gcs_tmp_out_path = os.path.join(ctx.tmp, gcs_embeddings_filename)

  # Process all the data, produce the final dataframes, build the embeddings and
  # return the embeddings dataframe.
  # During this process, the downloaded latest SVs and Descriptions data and the
  # final dataframe with SVs and Alternates are also written to local_merged_dir.
  embeddings_df = build(ctx, FLAGS.curated_input_dirs, local_merged_filepath,
                        dup_names_filepath, autogen_input_filepattern,
                        FLAGS.alternatives_filepattern)

  print(f"Saving locally to {gcs_tmp_out_path}")
  embeddings_df.to_csv(gcs_tmp_out_path, index=False)

  # Before uploading embeddings to GCS, validate them.
  print("Validating the built embeddings.")
  utils.validate_embeddings(embeddings_df, local_merged_filepath)
  print("Embeddings DataFrame is validated.")

  if not FLAGS.dry_run:
    # Finally, upload to the NL embeddings server's GCS bucket
    print("Attempting to write to GCS")
    print(f"\t GCS Path: gs://{FLAGS.bucket_name_v2}/{gcs_embeddings_filename}")
    blob = ctx.bucket.blob(gcs_embeddings_filename)
    # Since the files can be fairly large, use a 10min timeout to be safe.
    blob.upload_from_filename(gcs_tmp_out_path, timeout=600)
    print("Done uploading to gcs.")
    print(f"\t Embeddings Filename: {gcs_embeddings_filename}")
    print("\nNOTE: Please update embeddings.yaml with the Embeddings Filename")

  if FLAGS.vertex_ai_prediction_endpoint_id:
    with open(embeddings_index_tmp_out_path, 'w') as f:
      for _, row in embeddings_df.iterrows():
        write_row_to_jsonl(f, row)
    if not FLAGS.dry_run:
      # Update the jsonl file to GCS.
      # TODO: figure out which bucket to upload to and maybe include the
      # index building step here.
      pass
=======
  # Upload to GCS if needed
  fm.maybe_upload_to_gcs()
>>>>>>> staging

  if FLAGS.lancedb_output_path:
    version_dir = f'lancedb_{gcs_embeddings_filename.removesuffix(".csv")}'
    version_path = os.path.join(FLAGS.lancedb_output_path, version_dir)
    db = lancedb.connect(version_path)
    records = get_lancedb_records(embeddings_df)
    if not FLAGS.dry_run:
      # TODO: Upload to GCS
      pass
    db.create_table(_LANCEDB_TABLE, records)
    print(f'Generated LanceDB index in: {version_path}')


if __name__ == "__main__":
  app.run(main)
