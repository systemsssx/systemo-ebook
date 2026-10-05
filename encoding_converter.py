#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import sys
import json
import io
import shutil
from pathlib import Path


def detect_encoding(file_path):
    """检测文件编码"""
    with open(file_path, 'rb') as f:
        head = f.read(4)
    
    # BOM 检测
    if head.startswith(b'\xef\xbb\xbf'):
        return 'utf-8-sig'
    elif head.startswith(b'\xff\xfe'):
        return 'utf-16le'
    elif head.startswith(b'\xfe\xff'):
        return 'utf-16be'
    
    # 没有 BOM，尝试解码
    encodings = ['utf-8', 'gbk', 'gb18030', 'big5', 'shift-jis', 'euc-kr']
    for enc in encodings:
        try:
            with open(file_path, 'r', encoding=enc) as f:
                f.read()
                return enc
        except:
            continue
    
    return 'utf-8'


def convert_to_utf8(input_path, output_path=None):
    """
    将文本文件转换为 UTF-8 编码
    
    参数:
        input_path: 源文件路径
        output_path: 输出文件路径（可选，默认覆盖原文件）
    
    返回:
        success: bool
        output_path: 输出文件路径
        original_encoding: 原始编码
    """
    if not os.path.exists(input_path):
        return {
            'success': False,
            'error': f'文件不存在: {input_path}'
        }
    
    original_encoding = detect_encoding(input_path)
    print(f'📄 原始编码: {original_encoding}', file=sys.stderr)
    
    if original_encoding in ['utf-8', 'utf-8-sig']:
        return {
            'success': True,
            'output_path': input_path,
            'original_encoding': original_encoding,
            'note': '已是 UTF-8 编码'
        }
    
    try:
        with open(input_path, 'r', encoding=original_encoding) as f:
            content = f.read()
    except Exception as e:
        return {
            'success': False,
            'error': f'读取文件失败: {str(e)}',
            'original_encoding': original_encoding
        }
    
    if output_path is None:
        backup_path = input_path + '.bak'
        try:
            os.rename(input_path, backup_path)
            output_path = input_path
        except:
            shutil.copy2(input_path, backup_path)
            output_path = input_path
        print(f'📁 已备份原文件: {backup_path}', file=sys.stderr)
    
    try:
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f'✅ 已转换为 UTF-8: {output_path}', file=sys.stderr)
        return {
            'success': True,
            'output_path': output_path,
            'original_encoding': original_encoding,
            'note': f'已从 {original_encoding} 转换为 UTF-8'
        }
    except Exception as e:
        return {
            'success': False,
            'error': f'写入文件失败: {str(e)}',
            'original_encoding': original_encoding
        }


if __name__ == '__main__':
    try:
        input_data = sys.stdin.read()
        params = json.loads(input_data) if input_data else {}
        
        input_path = params.get('input_path')
        output_path = params.get('output_path', None)
        
        if not input_path:
            print(json.dumps({'success': False, 'error': '未指定输入文件'}))
            sys.exit(1)
        
        result = convert_to_utf8(input_path, output_path)
        print(json.dumps(result, ensure_ascii=False))
        
    except Exception as e:
        print(json.dumps({
            'success': False,
            'error': str(e)
        }))