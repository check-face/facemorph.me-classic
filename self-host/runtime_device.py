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


def unsupported_gpu_message(name, capability, arch_list, torch_version, cuda_version):
    """The plain refusal for a GPU this build's CUDA kernels cannot run, or None when it can.

    A pure function of the device and the wheel's arch list (torch.cuda.get_arch_list()). A card runs when the wheel
    ships kernels for its major architecture at or below its minor version (sm_XY runs on X.Z for Z >= Y), or PTX for
    an architecture at or below it (compute_XY, which the driver compiles forward). An empty arch list cannot be
    judged and is let through; the first kernel launch would report a mismatch.
    """
    major, minor = capability
    kernels = sorted(int(a[3:]) for a in arch_list if a.startswith('sm_') and a[3:].isdigit())
    ptx = [int(a[8:]) for a in arch_list if a.startswith('compute_') and a[8:].isdigit()]
    if not arch_list:
        return None
    if any(k // 10 == major and k % 10 <= minor for k in kernels) or any(p <= major * 10 + minor for p in ptx):
        return None
    supported = f'{kernels[0] // 10}.{kernels[0] % 10} to {kernels[-1] // 10}.x' if kernels else 'none'
    return (f'{name} has compute capability {major}.{minor}. This build (PyTorch {torch_version}, CUDA '
            f'{cuda_version}) supports compute capability {supported} ({", ".join(arch_list)}), so this GPU cannot '
            f'be used. Run on the CPU instead: {CPU_OVERRIDE}')


def check_capability(torch, index=0):
    """Stop at startup, with the plain message, on a GPU this build cannot run; never a later raw kernel error."""
    message = unsupported_gpu_message(torch.cuda.get_device_name(index), torch.cuda.get_device_capability(index),
                                      torch.cuda.get_arch_list(), torch.__version__, torch.version.cuda)
    if message:
        raise RuntimeError(message)


def device_status():
    import torch
    device = configure_device(torch)
    status = {'provider': device.type, 'torch_version': str(torch.__version__)}
    if device.type == 'cuda':
        status.update(gpu=torch.cuda.get_device_name(device),
                      gpu_capability=list(torch.cuda.get_device_capability(device)),
                      cuda_version=torch.version.cuda)
    return status
