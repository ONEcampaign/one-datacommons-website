#!/usr/bin/env bash
#
# A small utility script for incrementing the Helm Chart version -
# just the patch number for the time being.
#
chart_yaml="deploy/helm_charts/dc_website/Chart.yaml"

# Let's get the current Chart version:
current_version="$(grep version $chart_yaml | awk '{print $2}')"

# Display the current version
echo "Current Chart version is $current_version"

# Let's increment the patch version
new_version="$(echo "$current_version" | awk -vFS=. -vOFS=. '{$NF++;print}')"
echo "Incrementing the version to $new_version"

# Update the Chart.yaml
sed -i "s/version: $current_version/version: $new_version/" "$chart_yaml"

