set -ex

# Load environment variables and export them
source .env.example

export OPENAI_API_KEY
export OPENAI_BASE_URL
export MODEL
export MEM0_BASE_URL
export PYTHONUNBUFFERED

output_folder=${1:-"results"}
if [ ! -d "$output_folder" ]; then
    mkdir "$output_folder"
fi

curl -s -X POST "$MEM0_BASE_URL/reset_token_count"

# Record initial token count
curl -s "$MEM0_BASE_URL/token_count" > "./$output_folder/token1.json"

python3 run_experiments.py --method add --output_folder "$output_folder"
python3 run_experiments.py --method search --output_folder "$output_folder" --top_k 30

# Record final token count
curl -s "$MEM0_BASE_URL/token_count" > "./$output_folder/token2.json"

python3 evals.py --input_file "./$output_folder/results.json" --output_file "./$output_folder/evaluation_metrics.json"
python3 generate_scores.py "$output_folder"
