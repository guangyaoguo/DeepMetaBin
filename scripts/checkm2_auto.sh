#!/bin/bash

# 自动获取当前目录的路径
current_dir=$(pwd)

# 创建一个大文件夹来存放所有软件的 checkm2 结果
mkdir -p "$current_dir/all_checkm2_results"

# 获取可用线程数量
total_threads=$(nproc)*0.9
threads=$((total_threads))
# 定义软件名称和对应的bin路径
software_bins=(
    "metabat2:metabat2/*.fa"
    "metadecoder:metadecoder/*.fasta"
    "vamb_1000:vamb_1000/vamb_out/bins/*.fasta"
    "metacoag:metacoag/output_folder/bins/*.fasta"
    "maxbin:maxbin/*.fasta"
)

# 循环处理每个软件的bin路径
for software_bin in "${software_bins[@]}"; do
    software_name=$(echo $software_bin | cut -d':' -f1)
    bin_path="$current_dir/$(echo $software_bin | cut -d':' -f2)"
    fasta_files=$(ls $bin_path)
    # 执行 checkm2 命令来预测
    checkm2 predict --threads $threads --input $fasta_files --output-directory "$current_dir/all_checkm2_results/$software_name"
done

# 创建结果表格
echo -e "Software\tBin_Count_Completeness_GT_90_Contamination_LT_5" > "$current_dir/all_checkm2_results/comparison_table.tsv"

# 循环处理每个软件的结果
for software_bin in "$current_dir/all_checkm2_results"/*; do

    if [[ $software_bin == $current_dir'/all_checkm2_results/comparison_table.tsv' ]]; then
        continue
    fi
    software_name=$(basename $software_bin)
    bin_count=$(awk -F'\t' 'NR>1 && $2 > 90 && $3 < 5 {count++} END {print count}' "$software_bin/quality_report.tsv")
    echo -e "$software_name\t$bin_count" >> "$current_dir/all_checkm2_results/comparison_table.tsv"
done