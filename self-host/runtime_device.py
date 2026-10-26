"""Explicit CPU/CUDA selection shared by the generator and photo encoder."""
import os


def configure_device(torch):
    requested = os.environ.get('CHECKFACE_DEVICE', 'cpu').strip().lower()
    if requested not in ('cpu', 'cuda'):
        raise ValueError('CHECKFACE_DEVICE must be cpu or cuda')
    if requested == 'cuda':
        if not torch.cuda.is_available():
            raise RuntimeError('CUDA requested but unavailable. Use the GPU Compose override, '
                               'install the NVIDIA Container Toolkit and check your host driver.')
        check_capability(torch)
        # Required by deterministic CUDA matrix multiplication; set before CUDA use.
        os.environ.setdefault('CUBLAS_WORKSPACE_CONFIG', ':4096:8')
        torch.backends.cuda.matmul.allow_tf32 = False
        torch.backends.cudnn.allow_tf32 = False
        torch.backends.cudnn.benchmark = False
    return torch.device(requested)


CPU_OVERRIDE = 'ACCEPT_MODEL_TERMS=yes docker compose -f self-host/compose.yml up'


def check_capability(torch, index=0):
    """Refuse a GPU that this build's CUDA kernels cannot run, with a plain message instead of a later kernel error.

    A card runs when the wheel ships kernels for its exact architecture (sm_XY) or PTX for an architecture at or
    below it (compute_XY, which the driver compiles forward).
    """
    major, minor = torch.cuda.get_device_capability(index)
    arches = torch.cuda.get_arch_list()
    if not arches:
        return  # nothing to compare against; the first kernel launch reports any mismatch
    capability = major * 10 + minor
    kernels = sorted(int(a[3:]) for a in arches if a.startswith('sm_') and a[3:].isdigit())
    ptx = [int(a[8:]) for a in arches if a.startswith('compute_') and a[8:].isdigit()]
    if capability in kernels or any(p <= capability for p in ptx):
        return
    supported = (f'{kernels[0] // 10}.{kernels[0] % 10} to {kernels[-1] // 10}.{kernels[-1] % 10}'
                 if kernels else 'none')
    raise RuntimeError(
        f'{torch.cuda.get_device_name(index)} has compute capability {major}.{minor}. This build '
        f'(PyTorch {torch.__version__}, CUDA {torch.version.cuda}) supports compute capability {supported} '
        f'({", ".join(arches)}), so this GPU cannot be used. Run on the CPU instead: {CPU_OVERRIDE}')


def device_status():
    import torch
    device = configure_device(torch)
    status = {'provider': device.type, 'torch_version': str(torch.__version__)}
    if device.type == 'cuda':
        status.update(gpu=torch.cuda.get_device_name(device),
                      gpu_capability=list(torch.cuda.get_device_capability(device)),
                      cuda_version=torch.version.cuda)
    return status
