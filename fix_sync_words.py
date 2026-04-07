#!/usr/bin/env python3
import re
import ast

# 从frame_equalizer_impl.cc中复制的L-LTF符号数组
kHeader48Sc = [-26,-25,-24,-23,-22,
    -20,-19,-18,-17,-16,-15,-14,-13,-12,-11,-10,-9,-8,
    -6,-5,-4,-3,-2,-1,
     1,2,3,4,5,6,
     8,9,10,11,12,13,14,15,16,17,18,19,20,
    22,23,24,25,26]

kLltf48Sign = [
     1,1,-1,-1,1,
    -1,1,-1,1,1,1,1,1,1,-1,-1,1,1,
     1,-1,1,1,1,1,
     1,-1,-1,1,1,-1,
    -1,1,-1,-1,-1,-1,-1,1,1,-1,-1,1,-1,
    -1,1,1,1,1]

# 导频子载波和符号
kPilot4Sc = [-21, -7, 7, 21]
kLltfPilotSign = [1, -1, 1, 1]

# 基本复数值（保持与原配置相同的幅度/相位）
pos_value = (1.4719601443879746+1.4719601443879746j)
neg_value = (-1.4719601443879746-1.4719601443879746j)

def create_lltf_sequence(seq_idx):
    """创建L-LTF序列（seq_idx=0或1）"""
    # 创建64个零值的列表
    seq = [0.0] * 64

    # 填充数据子载波 (48个)
    for i, sc in enumerate(kHeader48Sc):
        bin_idx = sc + 32  # 转换为FFT bin索引
        sign = kLltf48Sign[i]
        seq[bin_idx] = pos_value if sign == 1 else neg_value

    # 填充导频子载波 (4个)
    for i, sc in enumerate(kPilot4Sc):
        bin_idx = sc + 32
        sign = kLltfPilotSign[i]
        seq[bin_idx] = pos_value if sign == 1 else neg_value

    return tuple(seq)

# 读取原文件
with open('examples/wifi_phy_hier.py', 'r') as f:
    content = f.read()

# 查找sync_words元组 - 使用更灵活的正则表达式
# 查找 digital.ofdm_carrier_allocator_cvc 调用，其中包含很长的元组
pattern = r'digital\.ofdm_carrier_allocator_cvc\([^)]+\(\(.*?\)\)[^)]+\)'
match = re.search(pattern, content, re.DOTALL)
if not match:
    print("错误: 无法找到digital.ofdm_carrier_allocator_cvc调用")
    exit(1)

allocator_call = match.group(0)
print(f"找到allocator_call，长度: {len(allocator_call)}")

# 提取sync_words元组：它是第4个参数（从0开始计数）
# 参数列表：fft_len, occupied_carriers, pilot_carriers, pilot_symbols, sync_words, len_tag_key, output_is_shifted
# 我们需要找到sync_words元组，即第五个参数（索引4）
# 简单方法：找到第一个'((0.0, 0.0'开始的元组
sync_pattern = r'\(\(0\.0,\s*0\.0[^)]+\)\)'
sync_match = re.search(sync_pattern, content, re.DOTALL)
if not sync_match:
    print("错误: 无法找到sync_words元组")
    exit(1)

sync_words_str = sync_match.group(0)
print(f"找到sync_words元组，长度: {len(sync_words_str)}")

# 解析原sync_words
try:
    old_sync_words = ast.literal_eval(sync_words_str)
    print(f"原sync_words包含{len(old_sync_words)}个序列")
    for i, seq in enumerate(old_sync_words):
        nonzero = sum(1 for v in seq if v != 0 and v != 0j and v != (0+0j))
        print(f"  序列{i}: 长度{len(seq)}, 非零值{nonzero}")
except Exception as e:
    print(f"解析错误: {e}")
    exit(1)

# 创建新的L-LTF序列
seq0 = create_lltf_sequence(0)
seq1 = create_lltf_sequence(1)

# 创建新的sync_words：替换序列0和1，保持其他序列不变
new_sync_words = list(old_sync_words)
new_sync_words[0] = seq0
new_sync_words[1] = seq1

# 转换为字符串表示，保持与原始格式相似的缩进
new_sync_words_str = "("
for i, seq in enumerate(new_sync_words):
    if i > 0:
        new_sync_words_str += ",\n"
    new_sync_words_str += str(seq)
new_sync_words_str += ")"

print(f"\n新sync_words元组长度: {len(new_sync_words_str)}")

# 替换原sync_words字符串
new_content = content.replace(sync_words_str, new_sync_words_str)

# 写回文件
with open('examples/wifi_phy_hier.py', 'w') as f:
    f.write(new_content)

print("成功更新wifi_phy_hier.py")

# 验证
print("\n验证修复后的序列0和1:")
with open('examples/wifi_phy_hier.py', 'r') as f:
    content = f.read()
    sync_match = re.search(sync_pattern, content, re.DOTALL)
    if sync_match:
        sync_words_str = sync_match.group(0)
        try:
            sync_words = ast.literal_eval(sync_words_str)
            for i in [0, 1]:
                seq = sync_words[i]
                nonzero = sum(1 for v in seq if v != 0 and v != 0j and v != (0+0j))
                print(f"  序列{i}: 非零值{nonzero}/64")
                # 检查占用子载波
                occupied = list(range(-26, -21)) + list(range(-20, -7)) + list(range(-6, 0)) + list(range(1, 7)) + list(range(8, 21)) + list(range(22, 27))
                zero_in_occupied = 0
                for sc in occupied:
                    bin_idx = sc + 32
                    if 0 <= bin_idx < 64 and (seq[bin_idx] == 0 or seq[bin_idx] == 0j or seq[bin_idx] == (0+0j)):
                        zero_in_occupied += 1
                print(f"    占用子载波中的零值: {zero_in_occupied}/52 (应为0)")
        except Exception as e:
            print(f"验证错误: {e}")