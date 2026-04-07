#!/usr/bin/env python3
"""
直接查找sync_words元组
"""

import re
import ast

def find_sync_words_direct():
    with open('examples/wifi_phy_hier.py', 'r') as f:
        content = f.read()

    # 查找 "((0.0, 0.0," 模式
    # 这是sync_words元组的典型开始
    start_markers = ["((0.0, 0.0,", "((0, 0,", "((0.0, 0.0"]

    for marker in start_markers:
        start_pos = content.find(marker)
        if start_pos != -1:
            print(f"找到开始标记 '{marker}' 在位置 {start_pos}")
            break
    else:
        print("错误: 找不到sync_words开始标记")
        return None

    # 从开始位置向前查找完整的((...))元组
    # 使用栈计数括号
    stack = 0
    found_start = False
    sync_words_str = ""

    for i in range(start_pos, len(content)):
        ch = content[i]
        sync_words_str += ch

        if ch == '(':
            stack += 1
            if stack == 2:
                found_start = True
        elif ch == ')':
            stack -= 1
            if stack == 0 and found_start:
                # 找到完整元组
                break

    print(f"提取的sync_words字符串长度: {len(sync_words_str)}")
    print(f"前200字符: {sync_words_str[:200]}...")
    print(f"后200字符: ...{sync_words_str[-200:]}")

    # 验证提取
    try:
        sync_words = ast.literal_eval(sync_words_str)
        print(f"✅ 成功解析sync_words，包含{len(sync_words)}个序列")

        # 分析每个序列
        for i, seq in enumerate(sync_words):
            if isinstance(seq, tuple):
                nonzero = sum(1 for v in seq if v != 0 and v != 0j and v != (0+0j))
                total = len(seq)
                print(f"序列{i}: 长度={total}, 非零值={nonzero}")

                if nonzero > 0 and nonzero < total:
                    # 分析非零值的位置和值
                    print(f"  非零值分析:")
                    nonzero_values = []
                    nonzero_positions = []
                    for j, v in enumerate(seq):
                        if v != 0 and v != 0j and v != (0+0j):
                            nonzero_values.append(v)
                            nonzero_positions.append(j - 32)  # 子载波索引

                    print(f"    非零值数量: {len(nonzero_values)}")
                    print(f"    非零值类型: {set(type(v).__name__ for v in nonzero_values)}")

                    # 对于序列0和1，检查具体值
                    if i < 2:
                        print(f"    前5个非零值: {nonzero_values[:5]}")
                        print(f"    前5个子载波索引: {nonzero_positions[:5]}")

                        # 检查是否只包含特定子载波
                        unique_positions = sorted(set(nonzero_positions))
                        print(f"    唯一子载波索引: {unique_positions}")
                        print(f"    子载波数量: {len(unique_positions)}")

                        # 检查缺失的子载波（预期52个）
                        expected_sc = [-26,-25,-24,-23,-22,
                            -20,-19,-18,-17,-16,-15,-14,-13,-12,-11,-10,-9,-8,
                            -6,-5,-4,-3,-2,-1,
                             1,2,3,4,5,6,
                             8,9,10,11,12,13,14,15,16,17,18,19,20,
                            22,23,24,25,26]

                        missing = [sc for sc in expected_sc if sc not in unique_positions]
                        print(f"    缺失子载波: {missing}")
                        print(f"    缺失数量: {len(missing)}")
            else:
                print(f"序列{i}: 不是元组，类型={type(seq)}")

        return sync_words_str, sync_words

    except Exception as e:
        print(f"❌ 解析错误: {e}")
        import traceback
        traceback.print_exc()
        return None

if __name__ == "__main__":
    result = find_sync_words_direct()
    if result:
        sync_words_str, sync_words = result
        print("\n✅ 分析完成")
    else:
        print("\n❌ 分析失败")