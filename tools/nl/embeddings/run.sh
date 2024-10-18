#!/bin/bash
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

<<<<<<< HEAD
function help {
  echo "Usage: -bflce <embeddings-size>"
  echo "$0 -b <embeddings-size> # 'small' or 'medium'. This option uses the base default sentence_transformer model."
  echo "$0 -f <embeddings-size> # 'small' or 'medium'. This option uses the finetuned model on PROD."
  echo "$0 -l <embeddings-size> <lancedb_output_path> # This option is used to generate the lanceDB index."
  echo "$0 -c <embeddings-size> <curated_input_dirs> <alternatives_filepattern> # This option creates custom embeddings (using the finetuned model in PROD)."
  echo "$0 -e <embeddings-size> <vertex_ai_endpoint_id> # This option creates embeddings using a Vertex AI model endpoint."
=======
# Usage function
usage() {
  echo "Usage: $0 -e <embeddings-name> -o <output_dir>"
  exit 1
>>>>>>> staging
}

# Parse options
while [[ "$#" -gt 0 ]]; do
  case $1 in
    -e)
        embeddings_name="$2"
        shift 2
        ;;
    -o)
        output_dir="$2"
        shift 2
        ;;
    *)
        echo "Unknown option: $1"
        usage
        ;;
  esac
done

if [ -z "$output_dir" ]; then
  output_dir="gs://datcom-nl-models/${embeddings_name}_$(date +'%Y_%m_%d_%H_%M_%S')/"
fi

<<<<<<< HEAD
while getopts beflc OPTION; do
  case $OPTION in
    b)
        echo -e "### Using the base default sentence_transformer model"
        FINETUNED_MODEL=""
        ;;
    e)
        MODEL_ENDPOINT_ID="$3"
        echo -e "### Using Vertex AI model endpoint $MODEL_ENDPOINT_ID"
        ;;
    f)
        echo -e "### Using the finetuned model from prod"
        FINETUNED_MODEL=$(curl -s https://raw.githubusercontent.com/datacommonsorg/website/master/deploy/nl/models.yaml | awk '$1=="tuned_model:"{ print $2; }')
        if [[ "$FINETUNED_MODEL" == "" ]]; then
          echo "Using option -f but could not retrieve an existing finetuned model from prod."
          exit 1
        else
          echo "Found finetuned model on prod: $FINETUNED_MODEL"
        fi
        ;;
    l)
        FINETUNED_MODEL=$(curl -s https://raw.githubusercontent.com/datacommonsorg/website/master/deploy/nl/models.yaml | awk '$1=="tuned_model:"{ print $2; }')
        if [[ "$FINETUNED_MODEL" == "" ]]; then
          echo "Using option -l but could not retrieve an existing finetuned model from prod."
          exit 1
        else
          echo "Found finetuned model on prod: $FINETUNED_MODEL"
        fi

        echo -e "### Generating LanceDB index"
        LANCEDB_OUTPUT_PATH="$3"
        if [[ "$LANCEDB_OUTPUT_PATH" == "" ]]; then
          help
          exit 1
        else
          echo "Generating LanceDB index inside: $LANCEDB_OUTPUT_PATH"
        fi
        ;;

    c)
      echo -e "### Using the finetuned model from prod with custom embeddings-size"
      CURATED_INPUT_DIRS="$3"
      if [[ "$CURATED_INPUT_DIRS" == "" ]]; then
        help
        exit 1
      else
        echo "Using the following local filenames as curated input: $CURATED_INPUT_DIRS"
      fi
      FINETUNED_MODEL=$(curl -s https://raw.githubusercontent.com/datacommonsorg/website/master/deploy/nl/models.yaml | awk '$1=="tuned_model:"{ print $2; }')
      if [[ "$FINETUNED_MODEL" == "" ]]; then
        echo "Using option -c but could not retrieve an existing finetuned model from prod."
        exit 1
      else
        echo "Found finetuned model on prod: $FINETUNED_MODEL"
      fi
      ALTERNATIVES_FILE_PATTERN="$4"
      if [[ "$ALTERNATIVES_FILE_PATTERN" == "" ]]; then
        echo "No alternatives files used."
      else
        echo "Using the following filepattern for files with alternatives: $ALTERNATIVES_FILE_PATTERN"
      fi
      ;;
    *)
        help
    esac
done
=======
# Print options
echo "Embeddings name: $embeddings_name"
echo "Output directory: $output_dir"
>>>>>>> staging

cd ../../..
python3 -m venv .env
source .env/bin/activate
pip3 install -r tools/nl/embeddings/requirements.txt -q

<<<<<<< HEAD
if [[ "$MODEL_ENDPOINT_ID" != "" ]];then
  python3 build_embeddings.py --embeddings_size=$2 \
    --vertex_ai_prediction_endpoint_id=$MODEL_ENDPOINT_ID \
    --curated_input_dirs="data/curated_input/main" \
    --autogen_input_basedir="" \
    --alternatives_filepattern=""

elif [[ "$CURATED_INPUT_DIRS" != "" ]]; then
  python3 build_embeddings.py --embeddings_size=$2 --finetuned_model_gcs=$FINETUNED_MODEL --curated_input_dirs=$CURATED_INPUT_DIRS --alternatives_filepattern=$ALTERNATIVES_FILE_PATTERN
elif [[ "$LANCEDB_OUTPUT_PATH" != "" ]]; then
  python3 build_embeddings.py --embeddings_size=$2 --finetuned_model_gcs=$FINETUNED_MODEL --lancedb_output_path=$LANCEDB_OUTPUT_PATH --dry_run=True
elif [[ "$FINETUNED_MODEL" != "" ]]; then
  python3 build_embeddings.py --embeddings_size=$2 --finetuned_model_gcs=$FINETUNED_MODEL
else
  python3 build_embeddings.py --embeddings_size=$2
fi
=======
export TOKENIZERS_PARALLELISM=false

python3 -m tools.nl.embeddings.build_embeddings \
  --embeddings_name=$embeddings_name \
  --output_dir=$output_dir

deactivate
cd tools/nl/embeddings
>>>>>>> staging
