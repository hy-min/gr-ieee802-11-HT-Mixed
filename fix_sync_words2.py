#!/usr/bin/env python3
import re
import ast

# L-LTF符号数组
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
    seq = [0.0] * 64
    for i, sc in enumerate(kHeader48Sc):
        bin_idx = sc + 32
        sign = kLltf48Sign[i]
        seq[bin_idx] = pos_value if sign == 1 else neg_value
    for i, sc in enumerate(kPilot4Sc):
        bin_idx = sc + 32
        sign = kLltfPilotSign[i]
        seq[bin_idx] = pos_value if sign == 1 else neg_value
    return tuple(seq)

with open('examples/wifi_phy_hier.py', 'r') as f:
    content = f.read()

# 查找sync_words元组 - 搜索特征模式
pattern = r'\(\(0\.0,\s*0\.0[^)]+\)\)[^)]+\)\)[^)]+\)\)[^)]+\)\)'
match = re.search(pattern, content, re.DOTALL)
if not match:
    # 尝试更简单的模式：找到第一个'((0.0, 0.0'到后面的'))), "packet_len"'
    pattern = r'\(\(0\.0,\s*0\.0.*?\)\)\s*,\s*"packet_len"'
    match = re.search(pattern, content, re.DOTALL)

if not match:
    print("无法找到sync_words元组")
    exit(1)

sync_words_str = match.group(0)
print(f"找到sync_words字符串，长度: {len(sync_words_str)}")
print("前200字符:", sync_words_str[:200])

# 提取元组部分：从'((0.0, 0.0'到'))), "packet_len"'之前的'))'
# 我们需要解析嵌套元组。简单方法：找到匹配的括号
def find_matching_paren(text, start):
    count = 0
    for i in range(start, len(text)):
        if text[i] == '(':
            count += 1
        elif text[i] == ')':
            count -= 1
            if count == 0:
                return i
    return -1

# 找到第一个'((0.0, 0.0'的位置
start = content.find('((0.0, 0.0')
if start == -1:
    print("无法找到'((0.0, 0.0'")
    exit(1)

# 找到匹配的闭合括号
end = find_matching_paren(content, start)
if end == -1:
    print("无法找到匹配的括号")
    exit(1)

# 提取元组字符串
tuple_str = content[start:end+1]
print(f"提取元组字符串，长度: {len(tuple_str)}")

# 解析元组
try:
    old_sync_words = ast.literal_eval(tuple_str)
    print(f"原sync_words包含{len(old_sync_words)}个序列")
    for i, seq in enumerate(old_sync_words):
        nonzero = sum(1 for v in seq if v != 0 and v != 0j and v != (0+0j))
        print(f"  序列{i}: 长度{len(seq)}, 非零值{nonzero}")
except Exception as e:
    print(f"解析错误: {e}")
    exit(1)

# 生成新的L-LTF序列
seq0 = create_lltf_sequence(0)
seq1 = create_lltf_sequence(1)

# 创建新的sync_words
new_sync_words = list(old_sync_words)
new_sync_words[0] = seq0
new_sync_words[1] = seq1

# 转换为字符串，保持格式
new_tuple_str = "("
for i, seq in enumerate(new_sync_words):
    if i > 0:
        new_tuple_str += ",\n"
    new_tuple_str += str(seq)
new_tuple_str += ")"

print(f"\n新元组字符串长度: {len(new_tuple_str)}")

# 替换
new_content = content[:start] + new_tuple_str + content[end+1:]

# 写回文件
with open('examples/wifi_phy_hier.py', 'w') as f:
    f.write(new_content)

print("成功更新wifi_phy_hier.py")

# 验证
print("\n验证修复:")
with open('examples/wifi_phy_hier.py', 'r') as f:
    content = f.read()
    start = content.find('((0.0, 0.0')
    if start != -1:
        end = find_matching_paren(content, start)
        if end != -1:
            tuple_str = content[start:end+1]
            try:
                sync_words = ast.literal_eval(tuple_str)
                for i in [0, 1]:
                    seq = sync_words[i]
                    nonzero = sum(1 for v in seq if v != 0 and v != 0j and v != (0+0j))
                    print(f"  序列{i}: 非零值{nonzero}/64")
            except Exception as e:
                print(f"验证错误: {e}")