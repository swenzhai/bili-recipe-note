from pathlib import Path
p=Path('/data04/swenz/ocr-on-demand/envs/dish-vllm-validation/lib/python3.12/site-packages/vllm/platforms/interface.py')
s=p.read_text()
old='            physical_device_id = device_ids[device_id]\n            return int(physical_device_id)'
new='''            physical_device_id = device_ids[device_id]
            if physical_device_id.startswith("GPU-"):
                import pynvml
                pynvml.nvmlInit()
                try:
                    handle = pynvml.nvmlDeviceGetHandleByUUID(physical_device_id)
                    return pynvml.nvmlDeviceGetIndex(handle)
                finally:
                    pynvml.nvmlShutdown()
            return int(physical_device_id)'''
assert s.count(old)==1
backup=p.with_suffix('.py.before-uuid-fix')
assert not backup.exists()
backup.write_text(s)
p.write_text(s.replace(old,new))
print('Patched isolated vLLM UUID-to-NVML index lookup; CUDA_VISIBLE_DEVICES unchanged')
