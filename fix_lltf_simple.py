#!/usr/bin/env python3
"""
简单修复：直接替换wifi_phy_hier.py中的L-LTF sync_words
"""

import re

# 读取文件
with open('examples/wifi_phy_hier.py', 'r') as f:
    lines = f.readlines()

# 找到sync_words开始的行
start_line = -1
for i, line in enumerate(lines):
    if 'sync_words:' in line:
        start_line = i
        print(f"找到sync_words在第{i+1}行: {line.strip()}")
        break

if start_line == -1:
    print("错误: 找不到sync_words")
    exit(1)

# 从start_line开始，找到sync_words参数的结束
# sync_words格式: ((...), (...), (...), (...))
# 我们需要匹配括号对
content_from_start = ''.join(lines[start_line:])

# 找到第一个'((',然后匹配到对应的'))'
# 使用栈计数括号
stack = 0
pos = 0
start_pos = content_from_start.find('((')
if start_pos == -1:
    print("错误: 找不到sync_words开始")
    exit(1)

pos = start_pos
stack = 0
for i in range(pos, len(content_from_start)):
    ch = content_from_start[i]
    if ch == '(':
        stack += 1
    elif ch == ')':
        stack -= 1
        if stack == 0:
            # 找到结束
            end_pos = i + 1
            break
else:
    print("错误: 无法匹配括号")
    exit(1)

# 提取原sync_words字符串
old_sync_words = content_from_start[start_pos:end_pos]
print(f"原sync_words长度: {len(old_sync_words)}")
print(f"前200字符: {old_sync_words[:200]}...")
print(f"后200字符: ...{old_sync_words[-200:]}")

# 解析原sync_words为Python元组
import ast
try:
    old_tuple = ast.literal_eval(old_sync_words)
    print(f"成功解析，包含{len(old_tuple)}个序列")
except Exception as e:
    print(f"解析错误: {e}")
    exit(1)

# 创建新的L-LTF序列
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

# 导频
kPilot4Sc = [-21, -7, 7, 21]
kLltfPilotSign = [1, -1, 1, 1]

# 基本值
pos_value = (1.4719601443879746+1.4719601443879746j)
neg_value = (-1.4719601443879746-1.4719601443879746j)

def create_lltf_seq():
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

# 创建新的sync_words元组
new_tuple = list(old_tuple)
new_tuple[0] = create_lltf_seq()
new_tuple[1] = create_lltf_seq()  # 序列1与序列0相同

# 转换为字符串（保持类似格式）
def seq_to_str(seq):
    """将序列转换为字符串，保持紧凑格式"""
    items = []
    for val in seq:
        if isinstance(val, complex):
            if val.real == 0 and val.imag == 0:
                items.append('0.0')
            else:
                # 保持原格式
                if val == pos_value:
                    items.append('(1.4719601443879746+1.4719601443879746j)')
                elif val == neg_value:
                    items.append('(-1.4719601443879746-1.4719601443879746j)')
                else:
                    items.append(str(val))
        elif isinstance(val, (int, float)):
            if val == 0:
                items.append('0.0' if isinstance(val, float) else '0')
            else:
                items.append(str(val))
        else:
            items.append(str(val))
    return '(' + ', '.join(items) + ')'

# 生成新的sync_words字符串
new_sync_words = '(' + ', '.join(seq_to_str(seq) for seq in new_tuple) + ')'
print(f"\n新sync_words长度: {len(new_sync_words)}")

# 验证新sync_words
try:
    test_tuple = ast.literal_eval(new_sync_words)
    print(f"验证成功，包含{len(test_tuple)}个序列")
    for i in [0, 1]:
        seq = test_tuple[i]
        nonzero = sum(1 for v in seq if v != 0 and v != 0j and v != (0+0j))
        print(f"  序列{i}: {nonzero}个非零值")
except Exception as e:
    print(f"验证错误: {e}")
    exit(1)

# 替换内容
new_content_from_start = (content_from_start[:start_pos] +
                         new_sync_words +
                         content_from_start[end_pos:])

# 重新构建完整文件
new_lines = lines[:start_line] + [new_content_from_start]

print(f"\n替换 {len(old_sync_words)} 字符 -> {len(new_sync_words)} 字符")

# 写回文件
with open('examples/wifi_phy_hier.py', 'w') as f:
    f.write(''.join(new_lines))

print("文件更新完成！")

# 快速检查
print("\n=== 检查修复 ===")
with open('examples/wifi_phy_hier.py', 'r') as f:
    # 找到sync_words并检查序列0
    content = f.read()
    # 简单检查
    if 'sync_words:' in content:
        print("sync_words存在")
    # 检查是否有足够的非零值
    if '(1.4719601443879746+1.4719601443879746j)' in content:
        pos_count = content.count('(1.4719601443879746+1.4719601443879746j)')
        neg_count = content.count('(-1.4719601443879746-1.4719601443879746j)')
        print(f"正值数量: {pos_count}, 负值数量: {neg_count}")
        print(f"总计: {pos_count + neg_count}个非零值 (期望: 52*2 = 104)")

print("\n✅ 修复完成！")