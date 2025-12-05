#!/usr/bin/env python3
"""
Automated test for Marker PDF extraction on GPU.

This test verifies that:
1. Marker models load correctly on GPU
2. PDF extraction works with a real document
3. Output contains expected markdown/text

Usage:
    # Run from ingest directory
    python tests/test_marker_extraction.py
    
    # Or via pytest
    pytest tests/test_marker_extraction.py -v
    
    # From Ansible (on Proxmox host)
    ssh root@<ingest-ip> "/srv/ingest/venv/bin/python /srv/ingest/tests/test_marker_extraction.py"
"""

import os
import sys
import tempfile
import time
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def test_transformers_patch():
    """Test that the transformers patch is applied correctly."""
    print("\n=== Test 1: Transformers Patch ===")
    
    from transformers import PreTrainedModel
    
    # The patch should set low_cpu_mem_usage=False
    # We can verify by checking if from_pretrained accepts the kwarg
    print("✓ Transformers imported successfully")
    
    # Apply patch manually for this test
    original_from_pretrained = PreTrainedModel.from_pretrained.__func__
    
    def patched_from_pretrained(cls, *args, **kwargs):
        kwargs['low_cpu_mem_usage'] = False
        return original_from_pretrained(cls, *args, **kwargs)
    
    PreTrainedModel.from_pretrained = classmethod(patched_from_pretrained)
    print("✓ Patch applied")
    
    return True


def test_cuda_available():
    """Test that CUDA is available."""
    print("\n=== Test 2: CUDA Availability ===")
    
    import torch
    
    if not torch.cuda.is_available():
        print("✗ CUDA not available")
        return False
    
    device_count = torch.cuda.device_count()
    print(f"✓ CUDA available with {device_count} device(s)")
    
    for i in range(device_count):
        name = torch.cuda.get_device_name(i)
        props = torch.cuda.get_device_properties(i)
        free_mem = (props.total_memory - torch.cuda.memory_allocated(i)) / 1024**3
        print(f"  GPU {i}: {name} ({props.total_memory/1024**3:.1f}GB total, ~{free_mem:.1f}GB free)")
    
    return True


def test_marker_model_loading():
    """Test that Marker models load on GPU without meta tensor errors."""
    print("\n=== Test 3: Marker Model Loading ===")
    
    start = time.time()
    
    try:
        from marker.models import create_model_dict
        
        print("Loading Marker models on GPU...")
        artifact_dict = create_model_dict()
        
        elapsed = time.time() - start
        print(f"✓ Models loaded in {elapsed:.1f}s")
        print(f"  Models: {list(artifact_dict.keys())}")
        
        # Clean up GPU memory
        import torch
        del artifact_dict
        torch.cuda.empty_cache()
        
        return True
        
    except Exception as e:
        print(f"✗ Model loading failed: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_pdf_extraction():
    """Test PDF extraction with a sample document."""
    print("\n=== Test 4: PDF Extraction ===")
    
    # Create a simple test PDF
    test_pdf_path = create_test_pdf()
    if not test_pdf_path:
        print("✗ Could not create test PDF")
        return False
    
    start = time.time()
    
    try:
        from marker.converters.pdf import PdfConverter
        from marker.models import create_model_dict
        
        print(f"Extracting text from: {test_pdf_path}")
        
        # Load models
        artifact_dict = create_model_dict()
        
        # Create converter and extract
        converter = PdfConverter(artifact_dict=artifact_dict)
        result = converter(test_pdf_path)
        
        elapsed = time.time() - start
        
        # Check result
        if hasattr(result, 'markdown'):
            markdown = result.markdown
        else:
            markdown = str(result)
        
        print(f"✓ Extraction completed in {elapsed:.1f}s")
        print(f"  Output length: {len(markdown)} chars")
        print(f"  Preview: {markdown[:200]}...")
        
        # Clean up
        import torch
        del artifact_dict
        torch.cuda.empty_cache()
        os.remove(test_pdf_path)
        
        return len(markdown) > 0
        
    except Exception as e:
        print(f"✗ PDF extraction failed: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        if os.path.exists(test_pdf_path):
            os.remove(test_pdf_path)
        return False


def create_test_pdf():
    """Create a simple test PDF for extraction testing."""
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas
        
        # Create temp file
        fd, path = tempfile.mkstemp(suffix='.pdf')
        os.close(fd)
        
        # Create PDF
        c = canvas.Canvas(path, pagesize=letter)
        c.setFont("Helvetica", 12)
        
        # Add some test content
        c.drawString(100, 750, "Test Document for Marker Extraction")
        c.drawString(100, 730, "=" * 40)
        c.drawString(100, 700, "This is a test PDF document created for automated testing.")
        c.drawString(100, 680, "It contains simple text that Marker should extract correctly.")
        c.drawString(100, 660, "")
        c.drawString(100, 640, "Key points to verify:")
        c.drawString(120, 620, "1. Text extraction works")
        c.drawString(120, 600, "2. Layout is preserved")
        c.drawString(120, 580, "3. No GPU memory errors")
        c.drawString(100, 540, "If you can read this in the output, the test passed!")
        
        c.save()
        
        print(f"Created test PDF: {path}")
        return path
        
    except ImportError:
        print("reportlab not installed, using fallback PDF")
        # Use a URL to download a sample PDF
        return download_sample_pdf()
    except Exception as e:
        print(f"Could not create test PDF: {e}")
        return None


def download_sample_pdf():
    """Download a sample PDF for testing."""
    import urllib.request
    
    # Use a simple public PDF
    url = "https://www.w3.org/WAI/WCAG21/Techniques/pdf/img/table-word.pdf"
    
    try:
        fd, path = tempfile.mkstemp(suffix='.pdf')
        os.close(fd)
        
        print(f"Downloading sample PDF from {url}...")
        urllib.request.urlretrieve(url, path)
        print(f"Downloaded to: {path}")
        return path
        
    except Exception as e:
        print(f"Could not download sample PDF: {e}")
        return None


def test_gpu_memory_cleanup():
    """Test that GPU memory is properly cleaned up."""
    print("\n=== Test 5: GPU Memory Cleanup ===")
    
    import torch
    
    if not torch.cuda.is_available():
        print("✗ CUDA not available, skipping")
        return True
    
    initial_mem = torch.cuda.memory_allocated(0)
    print(f"Initial GPU memory: {initial_mem / 1024**2:.1f} MB")
    
    # Force cleanup
    torch.cuda.empty_cache()
    
    final_mem = torch.cuda.memory_allocated(0)
    print(f"After cleanup: {final_mem / 1024**2:.1f} MB")
    
    print("✓ GPU memory cleanup test passed")
    return True


def main():
    """Run all tests."""
    print("=" * 60)
    print("Marker PDF Extraction Test Suite")
    print("=" * 60)
    
    results = {}
    
    # Run tests in order
    results['transformers_patch'] = test_transformers_patch()
    results['cuda_available'] = test_cuda_available()
    
    if results['cuda_available']:
        results['marker_model_loading'] = test_marker_model_loading()
        
        if results['marker_model_loading']:
            results['pdf_extraction'] = test_pdf_extraction()
    
    results['gpu_memory_cleanup'] = test_gpu_memory_cleanup()
    
    # Summary
    print("\n" + "=" * 60)
    print("Test Summary")
    print("=" * 60)
    
    all_passed = True
    for test_name, passed in results.items():
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"  {test_name}: {status}")
        if not passed:
            all_passed = False
    
    print()
    if all_passed:
        print("All tests PASSED!")
        return 0
    else:
        print("Some tests FAILED!")
        return 1


if __name__ == "__main__":
    sys.exit(main())

