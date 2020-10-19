from ipykernel.kernelapp import IPKernelApp
from .kernel import ASMKernel
IPKernelApp.launch_instance(kernel_class=ASMKernel)
