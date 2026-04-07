#!/usr/bin/env python3
"""
修复wifi_phy_hier.py中的L-LTF sync_words配置
问题：当前L-LTF符号只有12个子载波有值，导致信道估计失败
解决方案：为所有52个占用子载波填充正确的L-LTF符号值
"""

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

def analyze_current_sync_words(sync_words_str):
    """分析当前的sync_words配置"""
    try:
        sync_words = ast.literal_eval(sync_words_str)
        print(f"当前sync_words包含{len(sync_words)}个序列")

        for i, seq in enumerate(sync_words):
            nonzero = sum(1 for v in seq if v != 0 and v != 0j and v != (0+0j))
            print(f"  序列{i}: 长度{len(seq)}, 非零值{nonzero}")

            # 检查占用子载波
            occupied_carriers = (list(range(-26, -21)) + list(range(-20, -7)) +
                                list(range(-6, 0)) + list(range(1, 7)) +
                                list(range(8, 21)) + list(range(22, 27)))

            zero_in_occupied = 0
            for sc in occupied_carriers:
                bin_idx = sc + 32
                if 0 <= bin_idx < 64 and (seq[bin_idx] == 0 or seq[bin_idx] == 0j or seq[bin_idx] == (0+0j)):
                    zero_in_occupied += 1

            print(f"    占用子载波中的零值: {zero_in_occupied}/52")

        return sync_words
    except Exception as e:
        print(f"分析错误: {e}")
        return None

def create_fixed_sync_words():
    """创建修复后的sync_words"""
    # 序列0和1: L-LTF符号（两个相同的序列）
    seq0 = create_lltf_sequence(0)
    seq1 = create_lltf_sequence(1)

    # 序列2和3: 保持原样（L-SIG和HT-SIG）
    # 这些序列有正确的52个非零值，我们保持它们不变
    # 我们需要从原文件中提取它们

    # 读取原文件获取序列2和3
    with open('examples/wifi_phy_hier.py', 'r') as f:
        content = f.read()

    # 使用正则表达式找到sync_words
    # 查找从"((0.0, 0.0,"开始到"))), \"packet_len\", True)"结束的内容
    pattern = r'sync_words:\s*\(\(.*?\)\)\s*,\s*"packet_len"\s*,\s*True'
    match = re.search(pattern, content, re.DOTALL)
    if not match:
        print("错误: 无法找到sync_words参数")
        return None

    sync_words_str = match.group(0)
    print(f"找到sync_words参数（长度: {len(sync_words_str)}）")

    # 提取完整的sync_words元组
    sync_words_match = re.search(r'\(\(.*?\)\)', sync_words_str, re.DOTALL)
    if not sync_words_match:
        print("错误: 无法提取sync_words元组")
        return None

    old_sync_words_str = sync_words_match.group(0)

    try:
        old_sync_words = ast.literal_eval(old_sync_words_str)
        print(f"成功解析原sync_words，包含{len(old_sync_words)}个序列")

        # 创建新的sync_words：替换序列0和1，保持序列2和3不变
        new_sync_words = list(old_sync_words)
        new_sync_words[0] = seq0
        new_sync_words[1] = seq1

        # 转换为字符串表示
        # 我们需要保持与原始格式相似的格式
        new_sync_words_str = "(" + ",\n".join(str(seq) for seq in new_sync_words) + ")"

        return old_sync_words_str, new_sync_words_str

    except Exception as e:
        print(f"解析错误: {e}")
        return None

def apply_fix():
    """应用修复到文件"""
    print("=== 开始修复L-LTF sync_words配置 ===")

    # 创建修复后的sync_words
    result = create_fixed_sync_words()
    if not result:
        print("修复失败")
        return False

    old_str, new_str = result

    # 读取文件
    with open('examples/wifi_phy_hier.py', 'r') as f:
        content = f.read()

    # 检查旧字符串是否存在
    if old_str not in content:
        print("错误: 无法在文件中找到原sync_words字符串")
        return False

    # 替换
    new_content = content.replace(old_str, new_str)

    # 写回文件
    with open('examples/wifi_phy_hier.py', 'w') as f:
        f.write(new_content)

    print("成功更新wifi_phy_hier.py")
    print(f"替换了 {len(old_str)} 字符 -> {len(new_str)} 字符")

    # 验证修复
    print("\n=== 验证修复 ===")
    with open('examples/wifi_phy_hier.py', 'r') as f:
        # 提取新的sync_words进行分析
        new_content = f.read()
        pattern = r'sync_words:\s*\(\(.*?\)\)\s*,\s*"packet_len"\s*,\s*True'
        match = re.search(pattern, new_content, re.DOTALL)
        if match:
            sync_words_str = match.group(0)
            sync_words_match = re.search(r'\(\(.*?\)\)', sync_words_str, re.DOTALL)
            if sync_words_match:
                try:
                    sync_words = ast.literal_eval(sync_words_match.group(0))
                    print(f"新sync_words包含{len(sync_words)}个序列")

                    # 分析序列0和1
                    for i in [0, 1]:
                        seq = sync_words[i]
                        nonzero = sum(1 for v in seq if v != 0 and v != 0j and v != (0+0j))
                        print(f"  序列{i} (L-LTF{i}): 非零值{nonzero}/64")

                        # 检查占用子载波
                        occupied_carriers = (list(range(-26, -21)) + list(range(-20, -7)) +
                                            list(range(-6, 0)) + list(range(1, 7)) +
                                            list(range(8, 21)) + list(range(22, 27)))

                        zero_in_occupied = 0
                        for sc in occupied_carriers:
                            bin_idx = sc + 32
                            if 0 <= bin_idx < 64 and (seq[bin_idx] == 0 or seq[bin_idx] == 0j or seq[bin_idx] == (0+0j)):
                                zero_in_occupied += 1

                        print(f"    占用子载波中的零值: {zero_in_occupied}/52 (应为0)")

                except Exception as e:
                    print(f"验证错误: {e}")

    return True

if __name__ == "__main__":
    success = apply_fix()
    if success:
        print("\n✅ 修复完成！")
        print("下一步: 运行测试验证HT-SIG解析是否成功")
    else:
        print("\n❌ 修复失败")