#!/usr/bin/env python3
"""
最终修复脚本：替换wifi_phy_hier.py中的L-LTF sync_words序列
"""

import re
import ast

def create_correct_lltf_sequence():
    """创建正确的L-LTF序列"""
    # 来自frame_equalizer_impl.cc的子载波映射
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

    # 导频子载波
    kPilot4Sc = [-21, -7, 7, 21]
    kLltfPilotSign = [1, -1, 1, 1]

    # 使用与原文件相同的复数值
    pos_value = (1.4719601443879746+1.4719601443879746j)
    neg_value = (-1.4719601443879746-1.4719601443879746j)

    # 创建64点序列
    seq = [0.0] * 64

    # 填充数据子载波
    for i, sc in enumerate(kHeader48Sc):
        bin_idx = sc + 32  # 转换为FFT bin索引
        sign = kLltf48Sign[i]
        seq[bin_idx] = pos_value if sign == 1 else neg_value

    # 填充导频子载波
    for i, sc in enumerate(kPilot4Sc):
        bin_idx = sc + 32
        sign = kLltfPilotSign[i]
        seq[bin_idx] = pos_value if sign == 1 else neg_value

    return tuple(seq)

def find_and_replace_sync_words():
    """查找并替换sync_words中的序列0和1"""
    with open('examples/wifi_phy_hier.py', 'r') as f:
        content = f.read()

    # 查找sync_words元组开始位置
    start_marker = "((0.0, 0.0,"
    start_pos = content.find(start_marker)
    if start_pos == -1:
        print("错误: 找不到sync_words开始标记")
        return False

    print(f"找到sync_words开始位置: {start_pos}")

    # 提取完整元组
    stack = 0
    found_start = False
    sync_words_str = ""
    end_pos = start_pos

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
                end_pos = i + 1
                break

    if not found_start or stack != 0:
        print("错误: 无法提取完整sync_words元组")
        return False

    old_sync_words_str = sync_words_str
    print(f"原sync_words字符串长度: {len(old_sync_words_str)}")

    # 解析原sync_words
    try:
        sync_words = ast.literal_eval(old_sync_words_str)
        print(f"成功解析，包含{len(sync_words)}个序列")

        # 分析原序列
        for i in range(4):
            seq = sync_words[i]
            nonzero = sum(1 for v in seq if v != 0 and v != 0j and v != (0+0j))
            print(f"序列{i}: 非零值={nonzero}/64")

    except Exception as e:
        print(f"解析错误: {e}")
        return False

    # 创建正确的L-LTF序列
    correct_lltf_seq = create_correct_lltf_sequence()

    # 验证生成的序列
    nonzero = sum(1 for v in correct_lltf_seq if v != 0 and v != 0j and v != (0+0j))
    print(f"\n生成的L-LTF序列: 非零值={nonzero}/64 (期望52)")

    if nonzero != 52:
        print("警告: 生成的序列非零值数量不是52")

    # 计算非零子载波
    nonzero_sc = []
    for i, v in enumerate(correct_lltf_seq):
        if v != 0 and v != 0j and v != (0+0j):
            sc = i - 32
            nonzero_sc.append(sc)

    print(f"非零子载波数量: {len(nonzero_sc)}")
    print(f"前10个子载波: {sorted(nonzero_sc)[:10]}")

    # 创建新的sync_words：替换序列0和1，保持序列2和3不变
    new_sync_words = list(sync_words)
    new_sync_words[0] = correct_lltf_seq
    new_sync_words[1] = correct_lltf_seq  # 两个L-LTF序列相同

    # 转换为字符串表示，保持原格式
    def seq_to_str(seq):
        """将序列转换为字符串，保持与原文件相似的格式"""
        items = []
        for val in seq:
            if isinstance(val, complex):
                if val.real == 0 and val.imag == 0:
                    items.append('0.0')
                else:
                    # 保持原格式
                    if val == (1.4719601443879746+1.4719601443879746j):
                        items.append('(1.4719601443879746+1.4719601443879746j)')
                    elif val == (-1.4719601443879746-1.4719601443879746j):
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
    new_sync_words_str = '(' + ', '.join(seq_to_str(seq) for seq in new_sync_words) + ')'
    print(f"\n新sync_words字符串长度: {len(new_sync_words_str)}")

    # 验证新字符串可以正确解析
    try:
        test_sync_words = ast.literal_eval(new_sync_words_str)
        print(f"验证成功，包含{len(test_sync_words)}个序列")
        for i in range(4):
            seq = test_sync_words[i]
            nonzero = sum(1 for v in seq if v != 0 and v != 0j and v != (0+0j))
            print(f"  序列{i}: 非零值={nonzero}/64")
    except Exception as e:
        print(f"验证错误: {e}")
        return False

    # 替换内容
    new_content = content[:start_pos] + new_sync_words_str + content[end_pos:]
    print(f"\n替换完成:")
    print(f"原长度: {len(old_sync_words_str)} 字符")
    print(f"新长度: {len(new_sync_words_str)} 字符")

    # 写回文件
    with open('examples/wifi_phy_hier.py', 'w') as f:
        f.write(new_content)

    print(f"文件已更新: examples/wifi_phy_hier.py")

    return True

def verify_fix():
    """验证修复结果"""
    print("\n=== 验证修复 ===")
    with open('examples/wifi_phy_hier.py', 'r') as f:
        content = f.read()

    # 检查是否包含正确的非零值
    pos_count = content.count('(1.4719601443879746+1.4719601443879746j)')
    neg_count = content.count('(-1.4719601443879746-1.4719601443879746j)')
    total_nonzero = pos_count + neg_count

    print(f"正值数量: {pos_count}")
    print(f"负值数量: {neg_count}")
    print(f"总计非零值: {total_nonzero}")

    # 期望值: 每个L-LTF序列52个非零值，两个序列共104个
    # 但文件中有4个序列，所以会有更多非零值
    # 序列2和3也有非零值（但不是复数形式）

    # 检查序列0和1是否有足够的非零值
    # 简单检查：查找连续的0.0模式
    import re
    # 查找序列0（第一个((0.0, 0.0, ...)）
    pattern = r'\(\(0\.0,\s*0\.0'
    matches = re.findall(pattern, content)
    if matches:
        print(f"找到序列开始模式: {len(matches)}次")

    # 解析验证
    try:
        # 查找sync_words
        start_marker = "((0.0, 0.0,"
        start_pos = content.find(start_marker)
        if start_pos != -1:
            # 提取
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
                        break

            sync_words = ast.literal_eval(sync_words_str)
            print(f"\n最终验证:")
            for i, seq in enumerate(sync_words):
                nonzero = sum(1 for v in seq if v != 0 and v != 0j and v != (0+0j))
                print(f"序列{i}: 非零值={nonzero}/64")

                if i < 2 and nonzero < 52:
                    print(f"  ❌ 序列{i}非零值不足，只有{nonzero}")
                    return False
                elif i < 2:
                    print(f"  ✅ 序列{i}有{nonzero}个非零值")

        print("\n✅ 修复验证成功")
        return True

    except Exception as e:
        print(f"验证错误: {e}")
        return False

if __name__ == "__main__":
    print("=== 开始修复L-LTF sync_words配置 ===")

    # 备份原文件
    import shutil
    shutil.copy2('examples/wifi_phy_hier.py', 'examples/wifi_phy_hier.py.backup')
    print("已创建备份: examples/wifi_phy_hier.py.backup")

    # 执行修复
    if find_and_replace_sync_words():
        # 验证修复
        if verify_fix():
            print("\n🎉 修复完成！")
            print("下一步: 重新编译并测试HT-SIG解析")
        else:
            print("\n⚠️ 修复完成但验证失败，请手动检查")
    else:
        print("\n❌ 修复失败")