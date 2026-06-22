#!/usr/bin/env python3
"""
文件转二维码工具
将文件夹下的所有文件转换为二维码图片，支持大文件分块

使用方法:
    python file_to_qr.py <输入文件夹路径> [输出文件夹路径]

示例:
    python file_to_qr.py ./my_files ./qr_codes
"""

import os
import sys
import base64
import json
import qrcode
from pathlib import Path
from typing import List, Dict

# 每个二维码最大数据量（字节）
# QR Code Version 40 最大约2953字节（二进制模式）
# 使用Alphanumeric模式可以存储更多，但Base64需要Binary模式
# 考虑到JSON包装和Base64编码，并兼顾屏幕/摄像头像素限制，
# 设置为480字节以确保二维码密度适中，提升扫描成功率
MAX_CHUNK_SIZE = 384


def encode_file_to_chunks(file_path: str) -> List[Dict]:
    """
    将文件编码为多个数据块
    
    Args:
        file_path: 文件路径
    
    Returns:
        数据块列表，每个块包含元数据和Base64编码的数据
    """
    file_name = os.path.basename(file_path)
    file_size = os.path.getsize(file_path)
    
    # 读取文件内容并Base64编码
    with open(file_path, 'rb') as f:
        file_data = f.read()
    
    base64_data = base64.b64encode(file_data).decode('utf-8')
    
    # 计算需要的块数
    total_chunks = (len(base64_data) + MAX_CHUNK_SIZE - 1) // MAX_CHUNK_SIZE
    
    chunks = []
    for i in range(total_chunks):
        start = i * MAX_CHUNK_SIZE
        end = min(start + MAX_CHUNK_SIZE, len(base64_data))
        chunk_data = base64_data[start:end]
        
        # 创建数据块
        chunk = {
            "filename": file_name,
            "size": file_size,
            "index": i,
            "total": total_chunks,
            "data": chunk_data,
            "checksum": hash(chunk_data) & 0xFFFFFFFF  # 简单校验和
        }
        chunks.append(chunk)
    
    return chunks


def create_qr_code(data: str, output_path: str) -> bool:
    """
    创建二维码图片
    
    Args:
        data: 要编码的数据
        output_path: 输出图片路径
    
    Returns:
        是否成功
    """
    try:
        # 创建QRCode对象，使用最高容错率
        qr = qrcode.QRCode(
            version=None,  # 自动选择版本
            error_correction=qrcode.constants.ERROR_CORRECT_H,  # 最高容错率 30%
            box_size=10,
            border=4,
        )
        qr.add_data(data)
        qr.make(fit=True)
        
        # 生成图片
        img = qr.make_image(fill_color="black", back_color="white")
        img.save(output_path)
        
        return True
    except Exception as e:
        print(f"  错误: 生成二维码失败 - {e}")
        return False


def process_file(file_path: str, output_dir: str) -> bool:
    """
    处理单个文件，生成二维码
    
    Args:
        file_path: 输入文件路径
        output_dir: 输出目录
    
    Returns:
        是否成功
    """
    file_name = os.path.basename(file_path)
    print(f"处理文件: {file_name}")
    
    try:
        # 将文件分块
        chunks = encode_file_to_chunks(file_path)
        total_chunks = len(chunks)
        
        if total_chunks == 0:
            print(f"  警告: 文件为空")
            return False
        
        print(f"  文件大小: {os.path.getsize(file_path)} 字节")
        print(f"  分块数量: {total_chunks}")
        
        # 为每个块生成二维码
        base_name = os.path.splitext(file_name)[0]
        
        for i, chunk in enumerate(chunks):
            # 将数据块转换为JSON字符串
            chunk_json = json.dumps(chunk, ensure_ascii=False)
            
            # 生成二维码文件名
            if total_chunks == 1:
                qr_filename = f"{base_name}.png"
            else:
                qr_filename = f"{base_name}_part{i+1:03d}_of_{total_chunks:03d}.png"
            
            qr_path = os.path.join(output_dir, qr_filename)
            
            # 创建二维码
            if create_qr_code(chunk_json, qr_path):
                print(f"  生成二维码 [{i+1}/{total_chunks}]: {qr_filename}")
            else:
                print(f"  失败: 无法生成二维码 [{i+1}/{total_chunks}]")
                return False
        
        return True
        
    except Exception as e:
        print(f"  错误: 处理文件失败 - {e}")
        return False


def main():
    """主函数"""
    # 检查命令行参数
    if len(sys.argv) < 2:
        print(__doc__)
        print("错误: 请提供输入文件夹路径")
        sys.exit(1)
    
    input_dir = sys.argv[1]
    output_dir = sys.argv[2] if len(sys.argv) > 2 else "./qr_output"
    
    # 检查输入目录
    if not os.path.isdir(input_dir):
        print(f"错误: 输入路径不存在或不是目录: {input_dir}")
        sys.exit(1)
    
    # 创建输出目录
    os.makedirs(output_dir, exist_ok=True)
    
    print("=" * 60)
    print("文件转二维码工具")
    print("=" * 60)
    print(f"输入目录: {os.path.abspath(input_dir)}")
    print(f"输出目录: {os.path.abspath(output_dir)}")
    print(f"每块最大数据量: {MAX_CHUNK_SIZE} 字节")
    print("=" * 60)
    
    # 统计
    total_files = 0
    success_files = 0
    total_qr_codes = 0
    
    # 遍历输入目录中的所有文件
    for root, dirs, files in os.walk(input_dir):
        for filename in files:
            file_path = os.path.join(root, filename)
            
            # 跳过隐藏文件和二维码图片
            if filename.startswith('.') or filename.endswith('.png'):
                continue
            
            total_files += 1
            
            # 处理文件
            if process_file(file_path, output_dir):
                success_files += 1
                # 计算生成的二维码数量
                chunks = encode_file_to_chunks(file_path)
                total_qr_codes += len(chunks)
            
            print()
    
    # 打印统计信息
    print("=" * 60)
    print("处理完成!")
    print(f"总文件数: {total_files}")
    print(f"成功处理: {success_files}")
    print(f"失败: {total_files - success_files}")
    print(f"生成二维码总数: {total_qr_codes}")
    print("=" * 60)


if __name__ == "__main__":
    main()
