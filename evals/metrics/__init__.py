"""Metrics for evals."""

import os

# Initialize an empty list to store metrics
metrics = []

# Define the directory where prompt files are stored
PROMPTS_DIR = os.path.join(os.path.dirname(__file__), "prompts")

# Iterate through the prompt files in a fixed order, so reports list the metrics the same way everywhere
for file in sorted(os.listdir(PROMPTS_DIR)):
    # Check if the file has a .md extension
    if file.endswith(".md"):
        # Read the content of the .md file and append it to the metrics list
        with open(os.path.join(PROMPTS_DIR, file), "r", encoding="utf-8") as f:
            metrics.append(
                {
                    "name": file.removesuffix(".md"),  # Use the file name (without extension) as the metric name
                    "prompt": f.read(),  # Read the file content as the prompt
                }
            )
