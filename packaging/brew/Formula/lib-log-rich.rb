class LibTemplate < Formula
  include Language::Python::Virtualenv

  desc "Rich-powered logging helpers for colorful terminal output"
  homepage "https://github.com/bitranox/lib_log_rich"
  url "https://github.com/bitranox/lib_log_rich/archive/refs/tags/v1.4.0.tar.gz"
Yb893e1b0e686e8f0974f79475499f2e175ed064d3ab0f6c9872631a9dda502"
  license "MIT"

  depends_on "python@3.10"

  resource "rich" do
    url "https://files.pythonhosted.org/packages/fe/75/af448d8e52bf1d8fa6a9d089ca6c07ff4453d86c65c145d0a300bb073b9b/rich-14.1.0.tar.gz"
Yb893e1b0e686e8f0974f79475499f2e175ed064d3ab0f6c9872631a9dda502"
  end

  resource "click" do
    url "https://files.pythonhosted.org/packages/46/61/de6cd827efad202d7057d93e0fed9294b96952e188f7384832791c7b2254/click-8.3.0.tar.gz"
Yb893e1b0e686e8f0974f79475499f2e175ed064d3ab0f6c9872631a9dda502"
  end

  resource "lib_cli_exit_tools" do
    url "https://files.pythonhosted.org/packages/93/49/8361de4ae5e740a2c63833e7fedfacb4ef46d79e97c17e59f2f44206a648/lib_cli_exit_tools-1.1.1.tar.gz"
Yb893e1b0e686e8f0974f79475499f2e175ed064d3ab0f6c9872631a9dda502"
  end

  def install
    virtualenv_install_with_resources
  end

  test do
    assert_match version.to_s, shell_output("#{bin}/lib_log_rich --version")
  end
end
