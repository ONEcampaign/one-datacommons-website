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
"""Data Commons NL Experimentation routes"""

import json
import os

import flask
from flask import Blueprint
<<<<<<< HEAD
from flask import current_app
=======
from flask import redirect
>>>>>>> staging
from flask import render_template
from flask import request
from flask import url_for

import server.services.datacommons as dc

bp = Blueprint('nl', __name__, url_prefix='/nl')

<<<<<<< HEAD

@bp.route('/eval')
def eval_page():
  if not current_app.config['VERTEX_AI_MODELS']:
=======
_TEST_SHEET_ID = '1egx7AzQ47wxxQL_7oawWnnD-oIblx1i9xGmjslabZic'


@bp.route('/eval/embeddings')
def eval_embeddings():
  if os.environ.get('FLASK_ENV') not in ['local', 'test', 'autopush']:
>>>>>>> staging
    flask.abort(404)
  server_config = dc.nl_server_config()
  return render_template('/eval_embeddings.html',
                         server_config=json.dumps(server_config))


@bp.route('/eval/rig')
def eval_rig():
  return redirect(url_for('nl.eval_retrieval_generation'), code=302)


@bp.route('/eval/retrieval_generation')
def eval_retrieval_generation():
  if os.environ.get('FLASK_ENV') not in ['local', 'autopush']:
    flask.abort(404)
  sheet_id = request.args.get('sheet_id')
  if not sheet_id:
    return redirect(url_for('nl.eval_retrieval_generation',
                            sheet_id=_TEST_SHEET_ID),
                    code=302)
  return render_template('/eval_retrieval_generation.html', sheet_id=sheet_id)


@bp.route('/eval/retrieval_generation_sxs')
def eval_retrieval_generation_sxs():
  if os.environ.get('FLASK_ENV') not in ['local', 'autopush']:
    flask.abort(404)
  sheet_id_a = request.args.get('sheetIdA', '')
  sheet_id_b = request.args.get('sheetIdB', '')
  session_id = request.args.get('sessionId', '')
  return render_template('/eval_retrieval_generation_sxs.html',
                         sheet_id_a=sheet_id_a,
                         sheet_id_b=sheet_id_b,
                         session_id=session_id)
