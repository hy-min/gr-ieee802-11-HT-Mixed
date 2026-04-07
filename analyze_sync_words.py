#!/usr/bin/env python3
"""
分析wifi_phy_hier.py中的sync_words元组结构
"""

import re
import ast

def find_sync_words():
    with open('examples/wifi_phy_hier.py', 'r') as f:
        content = f.read()

    # 查找digital.ofdm_carrier_allocator_cvc调用
    pattern = r'digital\.ofdm_carrier_allocator_cvc\([^)]+\)'
    matches = re.findall(pattern, content, re.DOTALL)

    if not matches:
        print("错误: 找不到digital.ofdm_carrier_allocator_cvc调用")
        return None

    print(f"找到{len(matches)}个digital.ofdm_carrier_allocator_cvc调用")

    # 取第一个匹配
    call_str = matches[0]
    print(f"调用字符串长度: {len(call_str)}")
    print(f"前500字符:\n{call_str[:500]}...")

    # 解析参数：我们需要找到第4个参数（sync_words元组）
    # 参数格式：64, (list(...),), ((-21, -7, 7, 21), ), ((...), (...), (...), (...))
    # 我们需要匹配最外层的括号

    # 找到参数列表开始
    start = call_str.find('(') + 1
    if start == 0:
        print("错误: 找不到参数列表开始")
        return None

    # 使用栈计数匹配括号
    stack = 0
    param_start = start
    params = []
    current_param = ""

    for i in range(start, len(call_str)):
        ch = call_str[i]
        current_param += ch

        if ch == '(':
            stack += 1
        elif ch == ')':
            stack -= 1
            if stack < 0:
                # 函数调用结束
                current_param = current_param[:-1]  # 去掉最后的')'
                if current_param.strip():
                    params.append(current_param.strip())
                break
        elif ch == ',' and stack == 0:
            # 参数分隔符
            current_param = current_param[:-1]  # 去掉逗号
            if current_param.strip():
                params.append(current_param.strip())
            current_param = ""

    print(f"\n解析出{len(params)}个参数:")
    for i, param in enumerate(params):
        print(f"参数{i}: 长度={len(param)}, 前100字符={param[:100]}...")

    # 第4个参数应该是sync_words元组
    if len(params) >= 4:
        sync_words_str = params[3]
        print(f"\n第4个参数（sync_words）长度: {len(sync_words_str)}")

        # 尝试解析为Python元组
        try:
            sync_words = ast.literal_eval(sync_words_str)
            print(f"成功解析sync_words，包含{len(sync_words)}个序列")

            # 分析每个序列
            for i, seq in enumerate(sync_words):
                if isinstance(seq, tuple):
                    nonzero = sum(1 for v in seq if v != 0 and v != 0j and v != (0+0j))
                    total = len(seq)
                    print(f"序列{i}: 长度={total}, 非零值={nonzero}")

                    # 检查非零值的位置
                    if nonzero < 52 and nonzero > 0:
                        print(f"  非零值位置:")
                        nonzero_positions = []
                        for j, v in enumerate(seq):
                            if v != 0 and v != 0j and v != (0+0j):
                                # 计算对应的子载波索引
                                sc = j - 32  # FFT bin索引转子载波索引
                                nonzero_positions.append(sc)

                        print(f"  子载波索引: {nonzero_positions[:20]}...")

                else:
                    print(f"序列{i}: 不是元组，类型={type(seq)}")

            return sync_words_str, sync_words

        except Exception as e:
            print(f"解析sync_words错误: {e}")
            import traceback
            traceback.print_exc()

    return None

if __name__ == "__main__":
    result = find_sync_words()
    if result:
        sync_words_str, sync_words = result
        print("\n✅ 分析完成")
    else:
        print("\n❌ 分析失败")