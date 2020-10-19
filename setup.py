from setuptools import setup

setup(
      name='jupyter_asm_kernel',
      version='0.1.1',
      description='Minimalistic ASM kernel for Jupyter',
      author='Dan Bauman',
      author_email='bauman.85@osu.edu',
      license='MIT',
      classifiers=[
          'License :: OSI Approved :: MIT License',
      ],
      url='https://github.com/bauman/jupyter-asm-kernel/',
      packages=['jupyter_asm_kernel'],
      scripts=['jupyter_asm_kernel/install_asm_kernel'],
      keywords=['jupyter', 'notebook', 'kernel', 'asm'],
      install_requires=['pexpect'],
      include_package_data=True
)
