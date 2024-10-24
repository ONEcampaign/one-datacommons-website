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
"""Endpoints for Datacommons NL Experimentation"""

import json

import flask
from flask import Blueprint
from flask import request

<<<<<<< HEAD
from server.lib.cache import model_cache
from server.routes import TIMEOUT
import shared.model.api as model_api
=======
from server.services import datacommons as dc
>>>>>>> staging

bp = Blueprint('nl_api', __name__, url_prefix='/api/nl')


<<<<<<< HEAD
@bp.route('/encode-vector')
@model_cache.cached(timeout=TIMEOUT, query_string=True)
def encode_vector():
  """Retrieves the embedding vector for a given sentence and model.

    Valid model name can be found from `server/config/nl_page/nl_vertex_ai_models.yaml`
=======
@bp.route('/encode-vector', methods=['POST'])
def encode_vector():
  """Retrieves the embedding vector for a given query and model.
>>>>>>> staging
  """
  model = request.args.get('model')
  queries = request.json.get('queries', [])
  return json.dumps(dc.nl_encode(model, queries))


<<<<<<< HEAD
@bp.route('/vector-search')
@model_cache.cached(timeout=TIMEOUT, query_string=True)
def vector_search():
  """Performs vector search for a given sentence and model.

    Valid model name can be found from `server/config/nl_page/nl_vertex_ai_models.yaml`
=======
@bp.route('/search-vector', methods=['POST'])
def search_vector():
  """Performs vector search for a given query and embedding index.
>>>>>>> staging
  """
  idx = request.args.get('idx')
  if not idx:
    flask.abort(400, 'Must provide an `idx`')
  queries = request.json.get('queries')
  if not queries:
    flask.abort(400, 'Must provide a `queries` in POST data')

  return dc.nl_search_vars(queries, idx.split(','))
