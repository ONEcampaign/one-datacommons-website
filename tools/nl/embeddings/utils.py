# Copyright 2023 Google LLC
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
"""Common Utility functions for Embeddings."""

import csv
from dataclasses import asdict
from dataclasses import dataclass
import datetime as datetime
import glob
import hashlib
import itertools
import logging
import os
<<<<<<< HEAD
from pathlib import Path
from typing import Any, Dict, List, Tuple
=======
import time
from typing import Dict, List
>>>>>>> staging

import pandas as pd
import yaml

<<<<<<< HEAD
# Col names in the input files/sheets.
DCID_COL = 'dcid'
NAME_COL = 'Name'
DESCRIPTION_COL = 'Description'
CURATED_ALTERNATIVES_COL = 'Curated_Alternatives'
OVERRIDE_COL = 'Override_Alternatives'
ALTERNATIVES_COL = 'Alternatives'

DEFAULT_MODELS_BUCKET = 'datcom-nl-models'

# Col names in the concatenated dataframe.
COL_ALTERNATIVES = 'sentence'

_EMBEDDINGS_YAML_PATH = "../../../deploy/nl/embeddings.yaml"
_DEFAULT_EMBEDDINGS_INDEX_TYPE = "medium_ft"
=======
from nl_server import config_reader
from nl_server.config import Catalog
from nl_server.config import Env
from nl_server.config import IndexConfig
from nl_server.embeddings import EmbeddingsModel
from nl_server.model.create import create_embeddings_model
from shared.lib import constants
from shared.lib import gcs
from tools.nl.embeddings.file_manager import FileManager
>>>>>>> staging

_COL_DCID = 'dcid'
_COL_SENTENCE = 'sentence'
_CHUNK_SIZE = 100
<<<<<<< HEAD

_MODEL_ENDPOINT_RETRIES = 3

_GCS_PATH_PREFIX = "gs://"


def _is_gcs_path(path: str) -> bool:
  return path.strip().startswith(_GCS_PATH_PREFIX)


def _get_gcs_parts(gcs_path: str) -> Tuple[str, str]:
  return gcs_path[len(_GCS_PATH_PREFIX):].split('/', 1)


@dataclass
class ModelConfig:
  name: str
  # the model info as it would come from embeddings.yaml
  info: Dict[str, str]


@dataclass
# The info for a single embeddings index
class EmbeddingConfig:
  # the index info as it would come from embeddings.yaml
  index_config: Dict[str, str]
  model_config: ModelConfig
=======
_NUM_RETRIES = 3
_LANCEDB_TABLE = 'datacommons'
_MD5_SUM_FILE = 'md5sum.txt'
>>>>>>> staging


@dataclass
class PreIndex:
  text: str
  dcid: str  # ';' concatenated dcids


@dataclass
class Embedding:
  preindex: PreIndex
  vector: List[float]


def _chunk_list(data, chunk_size):
  it = iter(data)
  return iter(lambda: tuple(itertools.islice(it, chunk_size)), ())


def get_md5sum(file_path: str) -> str:
  with open(file_path, 'r') as f:
    return hashlib.md5(f.read().encode('utf-8')).hexdigest()


def get_model(catalog: Catalog, env: Env, model_name: str) -> EmbeddingsModel:
  logging.info("Loading model")
  model_config = catalog.models[model_name]
  if model_name in env.vertex_ai_models:
    vertex_ai_config = env.vertex_ai_models[model_name]
    model_config = config_reader.merge_vertex_ai_configs(
        model_config, vertex_ai_config)
  return create_embeddings_model(model_config)


<<<<<<< HEAD
def concat_alternatives(alternatives: List[str],
                        max_alternatives,
                        delimiter=";") -> str:
  alts = set(alternatives[0:max_alternatives])
  return f"{delimiter}".join(sorted(alts))


def split_alt_string(alt_string: str) -> List[str]:
  alts = []
  for alt in alt_string.split(";"):
    if alt:
      alts.append(alt.strip())
  return alts


def add_sv(name: str, sv: str, text2sv: Dict[str, str],
           dup_svs: List[List[str]]) -> None:
  osv = text2sv.get(name)
  if not osv or osv == sv:
    text2sv[name] = sv
    return

  # This is a case of duplicate SV.  Prefer the non sdg, human-curated, shorter SV.
  # Track it.
  pref, drop = sv, osv
  if sv.startswith('dc/topic/sdg'):
    # sv is an sdg topic. Prefer osv if osv is not an sdg topic. Otherwise, go
    # by dcid len.
    if not osv.startswith('dc/topic/sdg') or len(osv) <= len(sv):
      pref, drop = osv, sv
  elif ((osv.startswith('dc/') and sv.startswith('dc/')) or
        (not osv.startswith('dc/') and not sv.startswith('dc/'))):
    # Both SVs are autogen or both aren't. Go by dcid len.
    if len(osv) <= len(sv):
      pref, drop = osv, sv
  elif sv.startswith('dc/'):
    # sv is autogen, prefer osv.
    pref, drop = osv, sv

  text2sv[name] = pref
  dup_svs.append([pref, drop, name])


def dedup_texts(df: pd.DataFrame) -> Tuple[Dict[str, str], List[List[str]]]:
  """Dedup multiple texts mapped to the same DCID and return a list."""
  text2sv_dict = {}
  dup_sv_rows = [['PreferredSV', 'DroppedSV', 'DuplicateName']]
  for _, row in df.iterrows():
    sv = row[DCID_COL].strip()

    # All alternative sentences are retrieved from COL_ALTERNATIVES, which
    # are expected to be delimited by ";" (semi-colon).
    if COL_ALTERNATIVES in row:
      alternatives = row[COL_ALTERNATIVES].split(';')
      alternatives = [a.strip() for a in alternatives if a.strip()]
      for alt in alternatives:
        add_sv(alt, sv, text2sv_dict, dup_sv_rows)

  return (text2sv_dict, dup_sv_rows)


def _download_model_from_gcs(ctx: Context, model_folder_name: str) -> str:
  # TODO: Move download_folder from nl_server.gcs to shared.lib.gcs
  # and then use that function instead of this one.
  """Downloads a Sentence Tranformer model (or finetuned version) from GCS.

  Args:
    ctx: Context which has the GCS bucket information.
    model_folder_name: the GCS bucket name for the model.

  Returns the path to the local directory where the model was downloaded to.
  The downloaded model can then be loaded as:

  ```
      downloaded_model_path = _download_model_from_gcs(ctx, gcs_model_folder_name)
      model = SentenceTransformer(downloaded_model_path)
  ```
  """
  local_dir = os.path.join(ctx.tmp, DEFAULT_MODELS_BUCKET)
  # Get list of files
  blobs = ctx.bucket.list_blobs(prefix=model_folder_name)
  for blob in blobs:
    file_split = blob.name.split("/")
    directory = local_dir
    for p in file_split[0:-1]:
      directory = os.path.join(directory, p)
    Path(directory).mkdir(parents=True, exist_ok=True)

    if blob.name.endswith("/"):
      continue
    blob.download_to_filename(os.path.join(directory, file_split[-1]))

  return os.path.join(local_dir, model_folder_name)


def build_embeddings(ctx, text2sv: Dict[str, str]) -> pd.DataFrame:
  """Builds the embeddings dataframe.

  The output dataframe contains the embeddings columns (typically 384) + dcid + sentence.
  """
  texts = sorted(list(text2sv.keys()))

  if ctx.model:
    embeddings = ctx.model.encode(texts, show_progress_bar=True)
  else:
    embeddings = []
    for i, chuck in enumerate(chunk_list(texts, _CHUNK_SIZE)):
      logging.info('texts %d to %d', i * _CHUNK_SIZE, (i + 1) * _CHUNK_SIZE - 1)
      for i in range(_MODEL_ENDPOINT_RETRIES):
        try:
          resp = ctx.model_endpoint.predict(instances=chuck,
                                            timeout=600).predictions
          embeddings.extend(resp)
          break
        except Exception as e:
          logging.info('Exception %s', e)
  embeddings = pd.DataFrame(embeddings)
  embeddings[DCID_COL] = [text2sv[t] for t in texts]
  embeddings[COL_ALTERNATIVES] = texts
  return embeddings
=======
def load_existing_embeddings(embeddings_path: str) -> List[Embedding]:
  """Load computed embeddings existing embeddings path."""
  try:
    if gcs.is_gcs_path(embeddings_path):
      embeddings_path = gcs.maybe_download(embeddings_path)
    df = pd.read_csv(embeddings_path)
    embeddings = []
    for _, row in df.iterrows():
      dcid = row['dcid']
      sentence = row['sentence']
      vector = row.drop(labels=['dcid', 'sentence']).astype(float).tolist()
      embeddings.append(Embedding(PreIndex(text=sentence, dcid=dcid), vector))
    return embeddings
  except Exception as e:
    logging.error(e)
    return []
>>>>>>> staging


def build_and_save_preindexes(fm: FileManager) -> List[PreIndex]:
  """
<<<<<<< HEAD
  if _is_gcs_path(model_version):
    _, folder_name = _get_gcs_parts(model_version)
  else:
    folder_name = model_version

  tuned_model_path: str = os.path.join(ctx.tmp, DEFAULT_MODELS_BUCKET,
                                       folder_name)

  # Check if this model is already downloaded locally.
  if os.path.exists(tuned_model_path):
    print(f"Model already downloaded at path: {tuned_model_path}")
  else:
    print(
        f"Model not previously downloaded locally. Downloading from GCS: {folder_name}"
    )
    tuned_model_path = _download_model_from_gcs(ctx, folder_name)
    print(f"Model downloaded locally to: {tuned_model_path}")

  return tuned_model_path


def get_ft_model_from_gcs(ctx: Context,
                          model_version: str) -> SentenceTransformer:
  model_path = get_or_download_model_from_gcs(ctx, model_version)
  return SentenceTransformer(model_path)


def _get_default_ft_model(embeddings_yaml_file_path: str) -> ModelConfig:
  """Gets the default index's model version from embeddings.yaml.
  """
  return _get_default_ft_embeddings_info(embeddings_yaml_file_path).model_config


def get_default_ft_model() -> ModelConfig:
  """Gets the default index's model version from embeddings.yaml.
  """
  return _get_default_ft_model(_EMBEDDINGS_YAML_PATH)


def get_default_ft_embeddings_info() -> EmbeddingConfig:
  return _get_default_ft_embeddings_info(_EMBEDDINGS_YAML_PATH)


def _get_default_ft_embeddings_info(
    embeddings_yaml_file_path: str) -> EmbeddingConfig:
  with open(embeddings_yaml_file_path, "r") as f:
    data = yaml.full_load(f)
    if _DEFAULT_EMBEDDINGS_INDEX_TYPE not in data['indexes']:
      raise ValueError(f"{_DEFAULT_EMBEDDINGS_INDEX_TYPE} not found.")
    index_info = data['indexes'][_DEFAULT_EMBEDDINGS_INDEX_TYPE]
    model_name = index_info['model']
    model_info = ModelConfig(name=model_name, info=data['models'][model_name])
    return EmbeddingConfig(index_config=index_info, model_config=model_info)


def save_embeddings_yaml_with_only_default_ft_embeddings(
    embeddings_yaml_file_path: str,
    default_ft_embeddings_info: EmbeddingConfig):
  model_info = default_ft_embeddings_info.model_config
  data = {
      'version': 1,
      'indexes': {
          _DEFAULT_EMBEDDINGS_INDEX_TYPE:
              default_ft_embeddings_info.index_config
      },
      'models': {
          model_info.name: model_info.info
      }
  }
  with open(embeddings_yaml_file_path, "w") as f:
    yaml.dump(data, f)


def validate_embeddings(embeddings_df: pd.DataFrame,
                        output_dcid_sentences_filepath: str) -> None:
  # Verify that embeddings were created for all DCIDs and Sentences.
  dcid_sentence_df = pd.read_csv(
      create_file_handler(
          output_dcid_sentences_filepath).read_string_io()).fillna("")
  sentences = set()
  for alts in dcid_sentence_df["sentence"].values:
    for s in alts.split(";"):
      s = s.strip()
      if not s:
=======
  Build preindex records from a directory of CSV files.
  """
  text2sv: Dict[str, set[str]] = {}

  def _process(dcid: str, texts: List[str]):
    for text in texts:
      text = text.strip()
      if text == '':
>>>>>>> staging
        continue
      if text not in text2sv:
        text2sv[text] = set()
      text2sv[text].add(dcid)

  for file_name in glob.glob(fm.local_input_dir() + "/[!_]*.csv"):
    with open(file_name) as f:
      reader = csv.DictReader(f)
      for row in reader:
        dcid = row[_COL_DCID]
        texts = row[_COL_SENTENCE].split(';')
        _process(dcid, texts)

  for file_name in glob.glob(fm.local_input_dir() + "*.yaml"):
    with open(file_name) as f:
      data = yaml.safe_load(f)
      for dcid, texts in data.items():
        _process(dcid, texts)

  preindexes = [
      PreIndex(text, ';'.join(sorted(dcids)))
      for text, dcids in text2sv.items()
  ]
  preindexes.sort(key=lambda x: x.text)

  # Write preindexes as CSV
  with open(fm.preindex_csv_path(), 'w') as csvfile:
    csv_writer = csv.writer(csvfile, delimiter=',')
    csv_writer.writerow([_COL_SENTENCE, _COL_DCID])
    for preindex in preindexes:
      csv_writer.writerow([preindex.text, preindex.dcid])

  # Write md5sum of preindexes as a file
  with open(os.path.join(fm.local_output_dir(), _MD5_SUM_FILE), 'w') as f:
    f.write(get_md5sum(fm.preindex_csv_path()))

  return preindexes


def compute_embeddings(
    model: EmbeddingsModel,
    preindexes: List[PreIndex],
    existing_embeddings: List[Embedding],
) -> List[Embedding]:
  """Compute embeddings for the given preindexes

  Args:
    model: The embeddings model object,
    preindexes: A list of preindex to compute embeddings for
    existing_embeddings: A list of embeddings from previous run.
  Return:
    A list of embeddings for the preindexes.
  """
  logging.info("Compute embeddings with size %s", len(preindexes))
  start = time.time()

  result: List[Embedding] = []
  preindexes_to_compute: List[PreIndex] = []

  # Check each preindex, use existing embeddings vector if possible
  existing_embeddings_map = {x.preindex.text: x for x in existing_embeddings}
  for p in preindexes:
    if p.text in existing_embeddings_map:
      # Only use the saved sentence vector. The dcid might be different.
      result.append(Embedding(p, existing_embeddings_map[p.text].vector))
    else:
      preindexes_to_compute.append(p)

  # Compute embeddings with model inference
  logging.info("%d embeddings need computation", len(preindexes_to_compute))
  for i, chunk in enumerate(_chunk_list(preindexes_to_compute, _CHUNK_SIZE)):
    logging.info('texts %d to %d', i * _CHUNK_SIZE, (i + 1) * _CHUNK_SIZE - 1)
    for i in range(_NUM_RETRIES):
      try:
        resp = model.encode([x.text for x in chunk])
        if len(resp) != len(chunk):
          raise Exception(f'Expected {len(chunk)} but got {len(resp)}')
        for i, vector in enumerate(resp):
          result.append(
              Embedding(PreIndex(chunk[i].text, chunk[i].dcid), vector))
        break
      except Exception as e:
        logging.error('Exception: %s', e)

  # Sort result
  result.sort(key=lambda x: x.preindex.text)
  logging.info(f'Computing embeddings took {time.time() - start} seconds')
  return result


def save_embeddings_memory(local_dir: str, embeddings: List[Embedding]):
  """
  Save embeddings as csv file.
  """
  df = pd.DataFrame([x.vector for x in embeddings])
  df[_COL_DCID] = [x.preindex.dcid for x in embeddings]
  df[_COL_SENTENCE] = [x.preindex.text for x in embeddings]
  local_file = os.path.join(local_dir, constants.EMBEDDINGS_FILE_NAME)
  df.to_csv(local_file, index=False)
  logging.info("Saved embeddings to %s", local_file)


def save_embeddings_lancedb(local_dir: str, embeddings: List[Embedding]):
  # lancedb has issues in docker containers on certain platforms.
  # Importing it as a global import causes failures in build_embeddings on those platforms for Custom DC (CDC).
  # Since this method is never called for building CDC embeddings, we import it locally.
  # This will need to be addressed before we can support lancedb in CDC.
  import lancedb

  db = lancedb.connect(local_dir)
  records = [{
      _COL_DCID: x.preindex.dcid,
      _COL_SENTENCE: x.preindex.text,
      'vector': x.vector
  } for x in embeddings]
  db.create_table(_LANCEDB_TABLE, records)
  logging.info("Saved embeddings as lancedb file in %s", local_dir)


def save_index_config(fm: FileManager, index_config: IndexConfig):
  with open(fm.index_config_path(), 'w') as f:
    yaml.dump(asdict(index_config), f)
