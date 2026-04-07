#!/bin/bash

echo "================================================"
echo "decode_mac HT Mixed Mode 扩展最终验证"
echo "================================================"
echo ""

echo "1. 检查编译状态..."
if [ -f "build/lib/libgnuradio-ieee802_11.so" ]; then
    echo "  ✓ libgnuradio-ieee802_11.so 存在"
else
    echo "  ✗ libgnuradio-ieee802_11.so 不存在"
    exit 1
fi

if [ -f "build/python/bindings/ieee802_11_python.cpython-38-x86_64-linux-gnu.so" ]; then
    echo "  ✓ Python 模块存在"
else
    echo "  ✗ Python 模块不存在"
    echo "  搜索到的 Python 模块:"
    find build -name "*ieee802_11*.so" 2>/dev/null
    exit 1
fi

echo ""
echo "2. 运行 C++ 算法测试..."
./test_decode_mac_algo
if [ $? -eq 0 ]; then
    echo "  ✓ C++ 算法测试通过"
else
    echo "  ✗ C++ 算法测试失败"
    exit 1
fi

echo ""
echo "3. 运行 Python 算法测试..."
python3 test_decode_mac_algo.py
if [ $? -eq 0 ]; then
    echo "  ✓ Python 算法测试通过"
else
    echo "  ✗ Python 算法测试失败"
    exit 1
fi

echo ""
echo "4. 测试 Python 模块导入..."
python3 -c "
import sys
sys.path.insert(0, 'build/python/bindings')
sys.path.insert(0, 'build')
try:
    import ieee802_11
    print('  ✓ 成功导入 ieee802_11 模块')

    # 检查 decode_mac 函数
    if hasattr(ieee802_11, 'decode_mac'):
        print('  ✓ decode_mac 函数可用')
    else:
        print('  ✗ decode_mac 函数不可用')
        sys.exit(1)

except ImportError as e:
    print(f'  ✗ 导入失败: {e}')
    sys.exit(1)
except Exception as e:
    print(f'  ✗ 其他错误: {e}')
    sys.exit(1)
"

if [ $? -ne 0 ]; then
    exit 1
fi

echo ""
echo "5. 检查修改的文件..."
echo "  修改的文件:"
echo "  - lib/decode_mac.cc (主要修改)"
echo "  - examples/verify_tag_alignment.py (RPC 修复尝试)"
echo ""
echo "  新增的函数:"
echo "  - ht_deinterleave() - 通用 HT 去交织"
echo "  - hard_qpsk_bits() - QPSK 硬解调"
echo "  - hard_16qam_bits() - 16-QAM 硬解调"
echo "  - hard_64qam_bits() - 64-QAM 硬解调"
echo "  - mcs_to_encoding() - MCS 到编码映射"

echo ""
echo "6. 已知问题总结:"
echo "  - ✅ 算法正确性: 已验证"
echo "  - ✅ 编译: 成功"
echo "  - ✅ 模块导入: 成功"
echo "  - ⚠️  RPC 错误: 存在，阻止完整流图测试"
echo "  - ⚠️  系统集成测试: 受 RPC 错误阻塞"

echo ""
echo "7. 验证结果摘要:"
echo "  ================================================"
echo "  decode_mac HT Mixed 模式扩展 - 算法验证 ✓ PASS"
echo "  ================================================"
echo ""
echo "  核心算法修改已成功实现并验证:"
echo "  1. 通用 HT 去交织函数支持 MCS 0-7"
echo "  2. 多调制方式硬解调 (BPSK, QPSK, 16-QAM, 64-QAM)"
echo "  3. 正确的 MCS 到编码方式映射"
echo "  4. 参数计算修正"
echo ""
echo "  阻塞问题:"
echo "  - GNU Radio RPC 管理器错误阻止完整系统测试"
echo "  - 需要解决 RPC 问题后才能进行端到端验证"
echo ""
echo "  建议后续步骤:"
echo "  1. 调查并解决 RPC 错误"
echo "  2. 解决后运行完整系统测试 (wifi_phy_hier)"
echo "  3. 验证不同 MCS 值的端到端解码"

echo ""
echo "================================================"
echo "验证完成 - 算法层面修改已确认正确"
echo "================================================"